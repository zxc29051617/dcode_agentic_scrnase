/**
 * The reader's theme choice: what it can be, where it is kept, and how it is
 * applied before anything is painted.
 *
 * ## Three states, because "follow the system" is a real answer
 *
 * `system` is the default and is not a synonym for light. It means the page
 * keeps tracking `prefers-color-scheme`, including when the reader changes it
 * later in the day. `light` and `dark` are explicit and win over the system in
 * *both* directions — the CSS previously had an opt-out from dark but no way
 * to opt in, so choosing dark on a light machine did nothing at all.
 *
 * The choice is stored as the absence or presence of `data-theme` on the root
 * element, which is exactly what `globals.css` selects on. There is no second
 * representation of the same fact.
 *
 * ## Why a string of JavaScript
 *
 * The theme has to be on `<html>` before the first paint. Anything that waits
 * for React — a provider, an effect, a client component — runs after the
 * browser has already drawn one frame, and that frame is a white flash on the
 * way into a dark page. `THEME_BOOTSTRAP` is therefore inlined into the
 * document head and runs synchronously, ahead of the stylesheet doing
 * anything with it.
 *
 * It is deliberately tiny and total: any failure — a browser with storage
 * disabled, a value somebody hand-edited — leaves the attribute unset, which
 * is the `system` state and the same behaviour this app had before any of
 * this existed.
 */

export const THEME_STORAGE_KEY = "scrna.theme";

export const THEMES = ["system", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

export function isTheme(value: unknown): value is Theme {
  return typeof value === "string" && (THEMES as readonly string[]).includes(value);
}

/**
 * Read a stored value and say which theme it means.
 *
 * Anything unrecognised is `system`, not an error: a corrupt entry should put
 * the page back on the system default rather than leave it on whatever the
 * last valid value happened to be.
 */
export function themeFromStored(value: string | null | undefined): Theme {
  return isTheme(value) ? value : "system";
}

/** What `data-theme` should be for a choice — `null` meaning "no attribute". */
export function themeAttribute(theme: Theme): "light" | "dark" | null {
  return theme === "system" ? null : theme;
}

/**
 * Put the choice on the root element.
 *
 * Removing the attribute is what returns a page to following the system, and
 * it is why this sets/removes rather than always writing a value: an
 * attribute of `data-theme="system"` matches neither CSS rule and would leave
 * the page on the light palette regardless of the machine.
 */
export function applyTheme(theme: Theme, root: { setAttribute(name: string, value: string): void; removeAttribute(name: string): void }): void {
  const attribute = themeAttribute(theme);
  if (attribute === null) root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", attribute);
}

/**
 * The pre-paint bootstrap, as source to be inlined in the document head.
 *
 * Kept as one expression with no dependencies so it can run before any module
 * has loaded. It mirrors `applyTheme` above; `tests/theme_tokens.test.ts`
 * checks that the key and the accepted values here match the ones the module
 * exports, so the two cannot drift into disagreeing about where the choice
 * lives.
 */
export const THEME_BOOTSTRAP = `(function(){try{var t=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)});if(t==="light"||t==="dark"){document.documentElement.setAttribute("data-theme",t);}}catch(e){}})();`;
