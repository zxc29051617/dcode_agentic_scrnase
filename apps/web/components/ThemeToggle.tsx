"use client";

import { useEffect, useState } from "react";
import { THEMES, THEME_STORAGE_KEY, applyTheme, themeFromStored, type Theme } from "@/lib/theme";

/**
 * Light, dark, or whatever the machine says.
 *
 * Three buttons rather than a two-way switch, because "follow the system" is a
 * distinct answer and not the same as light. A binary toggle has to pick a
 * starting side, and whichever it picks it silently converts every reader who
 * had no preference into one who does — after which changing the laptop's own
 * setting stops moving this app.
 *
 * ## Why the state is read in an effect
 *
 * `localStorage` does not exist on the server, so the first render cannot know
 * the choice. Reading it in `useState`'s initialiser would make the server and
 * the client disagree about the first frame, which is the hydration error this
 * app has already been bitten by once (see `AppShell`'s aside preference).
 *
 * That does not cause a flash: the theme itself is applied before paint by the
 * inline script in `app/layout.tsx`. What arrives late is only which of these
 * three buttons is drawn as pressed, and until it does none of them is.
 */

const LABELS: Record<Theme, { text: string; title: string }> = {
  system: { text: "Auto", title: "follow this device's light or dark setting" },
  light: { text: "Light", title: "always light, whatever the device says" },
  dark: { text: "Dark", title: "always dark, whatever the device says" },
};

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    } catch {
      // Private browsing and a full quota both throw. The page still has
      // whatever the system asked for; only the memory of a choice is gone.
    }
    setTheme(themeFromStored(stored));
  }, []);

  function choose(next: Theme) {
    setTheme(next);
    applyTheme(next, document.documentElement);
    try {
      if (next === "system") window.localStorage.removeItem(THEME_STORAGE_KEY);
      else window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // As above: the choice applies to this page either way, it just will
      // not survive a reload. Not worth failing the click over.
    }
  }

  return (
    <div className="theme-toggle" role="group" aria-label="Colour theme" data-testid="theme-toggle">
      {THEMES.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => choose(option)}
          title={LABELS[option].title}
          // `aria-pressed` rather than a disabled state: the current choice is
          // still a button, so a reader can see which it is without losing the
          // ability to click the one they are already on.
          aria-pressed={theme === option}
          data-testid={`theme-${option}`}
        >
          {LABELS[option].text}
        </button>
      ))}
    </div>
  );
}
