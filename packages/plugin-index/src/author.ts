/**
 * Author field format, ported from typst/packages' `author.rs`.
 *
 * An author is a display name optionally followed by exactly one contact in angle
 * brackets: a GitHub handle (`<@handle>`), a URL, or an email address. Keeping the
 * contact machine-checkable is what lets a reviewer confirm that the person submitting
 * a plugin is the person the manifest credits.
 */

const GITHUB_HANDLE = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;
const EMAIL = /^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$/;
const URL_CHARS = /^[A-Za-z0-9\-_.~:/?#[\]@!$&'()*+,;=%]+$/;

/** Returns a problem description, or null when the author string is well-formed. */
export function checkAuthor(author: string): string | null {
  if (author.trim() !== author) return `author "${author}" has surrounding whitespace`;
  if (author === "") return "author must not be empty";

  const open = author.indexOf("<");
  if (open === -1) return null;

  const name = author.slice(0, open).trim();
  if (name === "") return `author "${author}" has a contact but no name`;
  if (!author.endsWith(">")) return `author "${author}" must end with '>' after the contact`;

  const contact = author.slice(open + 1, -1);
  if (contact.includes("<") || contact.includes(">")) {
    return `author "${author}" must contain exactly one contact in angle brackets`;
  }

  if (contact.startsWith("@")) {
    const handle = contact.slice(1);
    if (!GITHUB_HANDLE.test(handle)) return `"${handle}" is not a valid GitHub handle`;
    return null;
  }
  if (contact.startsWith("http")) {
    if (!contact.startsWith("https://")) return `contact URL "${contact}" must use https://`;
    if (!URL_CHARS.test(contact)) return `contact URL "${contact}" contains invalid characters`;
    return null;
  }
  if (!EMAIL.test(contact)) {
    return `contact "${contact}" is neither a GitHub handle (@name), an https URL, nor an email`;
  }
  return null;
}
