#!/bin/sh
exec bwrap --unshare-pid --unshare-ipc --unshare-uts "$@"
