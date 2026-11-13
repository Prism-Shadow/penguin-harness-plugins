"""Per-branch QEMU virtual machines with overlay rollback.

Each exploration branch gets its own VM: a qcow2 overlay is created on top of
a prepared base image, QEMU boots it under KVM, commands run over the serial
console, and the branch ends by killing QEMU and deleting the overlay. That
gives the paper's isolation property (a branch cannot see or affect another
branch's disk state) and its rollback property (the next branch starts from
the same base, at the cost of a ~200 KiB overlay instead of a 20 GiB copy).

This module does not pretend to be a security sandbox: the VM is disposable
and local, and the base image is operator-supplied. The control channel is an
autologin root shell on the serial console, which is fine for a throwaway
experiment VM and must not be used for anything else.

Requirements on the prepared base image (see log/vm_prepare.sh):
  - kernel command line contains console=ttyS0,115200n8
  - serial-getty@ttyS0 is enabled with autologin enabled
"""
import atexit
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_END = "__RSI_END__"


_BRANCHES = set()
_REGISTRY_LOCK = threading.Lock()
_HOOKS_INSTALLED = False


def _close_all_branches(*_args):
    """Best-effort teardown for every live branch (atexit / signal)."""
    with _REGISTRY_LOCK:
        branches = list(_BRANCHES)
    for branch in branches:
        try:
            branch.close()
        except Exception:
            pass


def _install_hooks():
    """Kill branch VMs when the interpreter exits or is asked to stop.

    ``timeout`` and Ctrl-C deliver SIGTERM/SIGINT; without this the QEMU
    process outlives the Python driver and keeps the overlay open.
    """
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    _HOOKS_INSTALLED = True
    atexit.register(_close_all_branches)
    if threading.current_thread() is not threading.main_thread():
        return

    def _handler(signum, frame):
        _close_all_branches()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass


def _socket_is_live(path):
    """A console socket with no listener is a leftover, not a live branch."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(path)
        return True
    except OSError:
        return False
    finally:
        probe.close()


def cleanup_stale_dirs(prefix="rsi-vm-", root="/tmp"):
    """Remove overlay directories left behind by a killed driver."""
    removed = 0
    for name in os.listdir(root):
        if not name.startswith(prefix):
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        sock_path = os.path.join(path, "console.sock")
        if os.path.exists(sock_path) and _socket_is_live(sock_path):
            continue  # a live branch owns it
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    return removed


_KEY_NAMES = {
    " ": "spc", "-": "minus", "_": "shift-minus", ".": "dot", ",": "comma",
    "/": "slash", ":": "shift-semicolon", ";": "semicolon", "=": "equal",
    "'": "apostrophe", '"': "shift-apostrophe", "(": "shift-9", ")": "shift-0",
    "\n": "ret", "\t": "tab",
}


def _key_name(character):
    """Map one printable character to a QEMU key name."""
    if character in _KEY_NAMES:
        return _KEY_NAMES[character]
    if character.isupper():
        return "shift-" + character.lower()
    if character.isascii() and character.isalnum():
        return character
    raise VMError("no key mapping for " + repr(character))


class VMError(RuntimeError):
    """The branch VM could not be started or lost its console."""


def _strip(text):
    return _ANSI.sub("", text)


def wrap_command(command, marker):
    """Wrap a command as one bash group that always reports its exit code.

    The closing brace goes on its own line: appending ``; }`` to a multi-line
    command leaves a stray semicolon after the last newline, which bash rejects
    (``syntax error near unexpected token ';'``) and then waits for more input
    until the console times out.
    """
    return ("{ " + str(command).rstrip("\n") + "\n} 2>&1; __rsi_rc=$?; "
            'echo "__RSI_RC__$__rsi_rc"; echo "' + marker + '"\n')


class VMBranch:
    """One disposable VM: overlay disk + serial console + command runner."""

    def __init__(self, base_image, *, memory_mb=2048, cpus=2, boot_timeout=180,
                 command_timeout=60, qemu="qemu-system-x86_64", accel="kvm",
                 sudo=False, keep_overlay=False, net_args=None):
        if not os.path.isfile(base_image):
            raise VMError("base image not found: " + str(base_image))
        self.base_image = os.path.abspath(base_image)
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.boot_timeout = boot_timeout
        self.command_timeout = command_timeout
        self.qemu = qemu
        self.accel = accel
        self.sudo = sudo
        self.keep_overlay = keep_overlay
        # Network arguments come from ``target_fence`` (or nothing at all).
        # A branch with no net_args has no NIC, which is the default because a
        # guest that cannot reach the network cannot exfiltrate the held-out
        # target.
        self.net_args = tuple(net_args or ())
        for argument in self.net_args:
            if "guestfwd" in argument and "restrict=on" not in argument:
                raise VMError("a guest relay requires restrict=on")
        self.dir = None
        self.overlay = None
        self.socket_path = None
        self.proc = None
        self.sock = None
        self.buffer = ""

    # --- lifecycle -------------------------------------------------------
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def start(self):
        self.dir = tempfile.mkdtemp(prefix="rsi-vm-")
        self.overlay = os.path.join(self.dir, "disk.qcow2")
        self.socket_path = os.path.join(self.dir, "console.sock")
        self.monitor_path = os.path.join(self.dir, "monitor.sock")
        subprocess.run([self.qemu.replace("qemu-system-x86_64", "qemu-img"),
                        "create", "-f", "qcow2", "-b", self.base_image,
                        "-F", "qcow2", self.overlay],
                       check=True, capture_output=True, text=True)
        argv = [
            self.qemu, "-accel", self.accel, "-cpu", "host",
            "-smp", str(self.cpus), "-m", str(self.memory_mb),
            # file.locking=off: a verifier clone opens the Actor's overlay as
            # its backing file while the Actor VM still holds the qcow2 write
            # lock, which QEMU otherwise refuses ("Failed to get shared
            # \"write\" lock"). Every branch lives in its own temp directory
            # that this process owns, so the lock protects nothing here.
            # cache=none: writes reach the file, so a verifier clone reading the
            # same overlay as its backing file sees committed data instead of
            # whatever is still sitting in this process's page cache.
            "-drive", "file={},if=virtio,format=qcow2,file.locking=off,cache=none"
                      .format(self.overlay),
            "-display", "none", "-monitor", "none", "-no-reboot",
            "-chardev", "socket,id=con,path={},server=on,wait=off"
                        .format(self.socket_path),
            "-serial", "chardev:con",
            # A monitor channel makes the screen observable (screendump), which
            # is how a rendered claim gets settled.
            "-monitor", "unix:{},server=on,wait=off".format(self.monitor_path),
        ]
        # QEMU otherwise creates a default user-mode NIC even when no
        # explicit network arguments were supplied.
        argv.extend(self.net_args if self.net_args else ("-nic", "none"))
        if self.sudo:
            argv = ["sudo", "-n"] + argv
        _install_hooks()
        self.proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE)
        with _REGISTRY_LOCK:
            _BRANCHES.add(self)
        self._connect()
        self._wait_for_shell()
        return self

    def _connect(self):
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.exists(self.socket_path):
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    sock.connect(self.socket_path)
                except OSError:
                    sock.close()
                    time.sleep(0.2)
                    continue
                sock.settimeout(0.5)
                self.sock = sock
                return
            if self.proc.poll() is not None:
                err = (self.proc.stderr.read() or b"").decode("utf-8", "replace")
                raise VMError("qemu exited before the console was ready: " + err[:400])
            time.sleep(0.2)
        raise VMError("console socket never appeared: " + self.socket_path)

    def _read_until(self, marker, timeout):
        deadline = time.time() + timeout
        while marker not in self.buffer:
            if time.time() > deadline:
                raise VMError("timed out waiting for " + marker +
                              "; recent console: " + self.buffer[-300:])
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                if self.proc.poll() is not None:
                    raise VMError("qemu exited while waiting for " + marker)
                continue
            if not chunk:
                raise VMError("console closed while waiting for " + marker)
            self.buffer += chunk.decode("utf-8", "replace")
        head, _, self.buffer = self.buffer.partition(marker)
        return head

    def _wait_for_shell(self):
        # The autologin shell prints a root prompt; nudge it once.
        deadline = time.time() + self.boot_timeout
        while time.time() < deadline:
            try:
                chunk = self.sock.recv(65536)
                self.buffer += chunk.decode("utf-8", "replace")
            except socket.timeout:
                self._send("\n")
                continue
            if re.search(r"root@[^\n]*[#$]", _strip(self.buffer)):
                self._send("stty -echo; export TERM=dumb; PS1=''; PROMPT_COMMAND=''\n")
                self._drain()
                self.buffer = ""
                return
            if self.proc.poll() is not None:
                raise VMError("qemu exited during boot")
        raise VMError("no root shell on the serial console within "
                      + str(self.boot_timeout) + "s; console tail: "
                      + _strip(self.buffer)[-300:])

    def _send(self, text):
        self.sock.sendall(text.encode())

    def _drain(self, quiet=0.4, limit=20.0):
        """Read and discard console output until it goes quiet."""
        self.sock.settimeout(quiet)
        deadline = time.time() + limit
        try:
            while time.time() < deadline:
                try:
                    chunk = self.sock.recv(65536)
                except socket.timeout:
                    return
                if not chunk:
                    return
        finally:
            self.sock.settimeout(0.5)

    # --- work -------------------------------------------------------------
    def run(self, command, timeout=None):
        """Run one shell command; return (exit_code, cleaned_output)."""
        timeout = timeout or self.command_timeout
        marker = _END + str(int(time.time() * 1000) % 1000000)
        self.buffer = ""
        self._send(wrap_command(command, marker))
        raw = _strip(self._read_until(marker, timeout))
        match = re.search(r"__RSI_RC__(-?\d+)", raw)
        code = int(match.group(1)) if match else -1
        text = raw[:match.start()] if match else raw
        text = text.replace("__RSI_RC__", "")
        lines = text.splitlines()
        # The first line is the echoed command; drop it when it matches.
        if lines and command.strip() and command.strip() in lines[0]:
            lines = lines[1:]
        return code, "\n".join(lines).strip("\n")

    def _monitor(self, command, timeout=15):
        """Send one command to the QEMU monitor and return its reply text."""
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(timeout)
        try:
            probe.connect(self.monitor_path)
        except OSError as exc:
            probe.close()
            raise VMError("monitor unavailable: " + repr(exc)) from exc
        try:
            try:
                probe.recv(65536)          # greeting
            except socket.timeout:
                pass
            probe.sendall((command + "\n").encode())
            buffer = ""
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    chunk = probe.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", "replace")
                if "Error" in buffer or buffer.count("(qemu)") >= 2:
                    break
            if "Error" in buffer:
                raise VMError("monitor rejected {}: {}".format(command, buffer[:200]))
            return buffer
        finally:
            probe.close()

    def pause(self):
        """Freeze the vCPUs so the disk is consistent for another reader."""
        if getattr(self, "_paused", False):
            return
        self._monitor("stop")
        self._paused = True

    def resume(self):
        """Thaw the vCPUs after an inspection."""
        if not getattr(self, "_paused", False):
            return
        self._monitor("cont")
        self._paused = False

    def sync(self, timeout=30):
        """Ask the guest to flush its page cache to the disk."""
        return self.run("sync", timeout=timeout)

    # --- graphical input (the GUI action space) ---------------------------
    _ABS_SCALE = 0x7FFF

    def key(self, keys):
        """Send a key chord, e.g. "ctrl-alt-t" or "Return"."""
        if not isinstance(keys, str) or not keys.strip():
            raise VMError("key chord must be a nonempty string")
        return self._monitor("sendkey " + keys.strip())

    def move_pointer(self, x, y, width, height):
        """Move the pointer to an absolute position in a WxH viewport."""
        for axis, value, extent in (("x", x, width), ("y", y, height)):
            scaled = max(0, min(self._ABS_SCALE,
                                int(value * self._ABS_SCALE / max(1, extent - 1))))
            self._monitor('input-send-event events=[{{"type":"abs","data":'
                          '{{"axis":"{}","value":{}}}}}]'.format(axis, scaled))
        return (x, y)

    def click(self, x, y, width, height, button="left"):
        """Move to (x, y) and click once."""
        if button not in ("left", "right", "middle"):
            raise VMError("button must be left, right or middle")
        self.move_pointer(x, y, width, height)
        self._monitor('input-send-event events=[{{"type":"btn","data":'
                      '{{"down":true,"button":"{}"}}}}]'.format(button))
        self._monitor('input-send-event events=[{{"type":"btn","data":'
                      '{{"down":false,"button":"{}"}}}}]'.format(button))
        return (x, y)

    def type_text(self, text):
        """Type a literal ASCII string (letters, digits, space, common punctuation)."""
        if not isinstance(text, str) or not text:
            raise VMError("text must be a nonempty string")
        for character in text:
            self._monitor("sendkey " + _key_name(character))
        return len(text)

    def screendump(self, path, timeout=15):
        """Ask QEMU's monitor for a PPM screenshot of the branch screen."""
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(timeout)
        try:
            probe.connect(self.monitor_path)
        except OSError as exc:
            probe.close()
            raise VMError("screen channel unavailable: " + repr(exc)) from exc
        try:
            try:
                probe.recv(65536)          # the monitor greeting
            except socket.timeout:
                pass
            probe.sendall(("screendump {}\n".format(path)).encode())
            buffer = ""
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    chunk = probe.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", "replace")
                if "Error" in buffer or buffer.count("(qemu)") >= 2:
                    break
            if "Error" in buffer:
                raise VMError("screendump failed: " + buffer.strip()[:200])
        finally:
            probe.close()
        return path

    def close(self):
        with _REGISTRY_LOCK:
            _BRANCHES.discard(self)
        if getattr(self, "_closed", False):
            return
        self._closed = True
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if self.proc is not None and self.proc.stderr is not None:
            try:
                self.proc.stderr.close()
            except OSError:
                pass
        self.proc = None
        if self.dir is not None and not self.keep_overlay:
            shutil.rmtree(self.dir, ignore_errors=True)
        self.dir = None

    @property
    def overlay_bytes(self):
        if self.overlay and os.path.exists(self.overlay):
            return os.path.getsize(self.overlay)
        return 0
