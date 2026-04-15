const STORAGE_KEY = "pocketwatch_user_profile";

export type UserProfile = {
  displayName: string;
  email: string;
};

function titleCaseWords(s: string): string {
  return s
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

export function saveUserProfileFromSignup(fullName: string, email: string): void {
  const trimmed = fullName.trim();
  const displayName =
    trimmed || email.split("@")[0]?.replace(/[._-]+/g, " ").trim() || "User";
  const payload: UserProfile = {
    displayName: titleCaseWords(displayName),
    email: email.trim().toLowerCase(),
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

export function saveUserProfileFromLogin(email: string): void {
  const local = email.split("@")[0]?.replace(/[._-]+/g, " ").trim() || "user";
  const payload: UserProfile = {
    displayName: titleCaseWords(local),
    email: email.trim().toLowerCase(),
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

export function loadUserProfile(): UserProfile | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Partial<UserProfile>;
    if (typeof p.displayName === "string" && typeof p.email === "string") {
      return { displayName: p.displayName, email: p.email };
    }
    return null;
  } catch {
    return null;
  }
}

export function clearUserProfile(): void {
  localStorage.removeItem(STORAGE_KEY);
}

/** First word of display name for “Welcome back, …” */
export function getGreetingFirstName(): string {
  const p = loadUserProfile();
  if (!p?.displayName?.trim()) return "there";
  const first = p.displayName.trim().split(/\s+/)[0];
  return first || "there";
}

export function getUserInitials(): string {
  const p = loadUserProfile();
  if (!p?.displayName?.trim()) {
    if (p?.email) return p.email.slice(0, 2).toUpperCase();
    return "?";
  }
  const parts = p.displayName.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase();
  }
  const word = parts[0];
  if (word.length >= 2) return word.slice(0, 2).toUpperCase();
  return (word.charAt(0) + (p.email?.charAt(0) || "")).toUpperCase();
}
