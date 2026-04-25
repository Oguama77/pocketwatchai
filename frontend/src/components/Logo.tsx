import type { SVGProps } from "react";
import { cn } from "@/lib/utils";

/**
 * Single source of truth for the PocketWatch brand mark.
 *
 * The glyph is a stylised pocket-watch (crown + bow at the top, circular
 * face) whose dial contains an upward trend line — the brand promise
 * compressed into one icon: "your money, on the rise". Used in the
 * sidebar, the auth pages, and anywhere a small brand mark is needed.
 */
export function LogoIcon({ className, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      focusable="false"
      className={cn("text-current", className)}
      {...props}
    >
      {/* Bow (the loop you'd hang the watch from) */}
      <path d="M11 1.6h2a1 1 0 0 1 1 1V3.4h-4V2.6a1 1 0 0 1 1-1z" />
      {/* Crown / stem */}
      <path d="M10.4 3.4h3.2v1.4h-3.2z" fill="currentColor" stroke="none" />
      {/* Watch face */}
      <circle cx="12" cy="14" r="7.2" />
      {/* 12-o-clock hour mark */}
      <circle cx="12" cy="9" r="0.7" fill="currentColor" stroke="none" />
      {/* Upward trend line inside the dial */}
      <path d="M8 16.4 10.6 13.7 13 15.1 16 11.6" />
    </svg>
  );
}

type LogoMarkProps = {
  /** Visual size preset. `sm` matches the sidebar header, `lg` matches the auth pages. */
  size?: "sm" | "md" | "lg";
  className?: string;
};

const CONTAINER: Record<NonNullable<LogoMarkProps["size"]>, string> = {
  sm: "w-8 h-8 rounded-lg",
  md: "w-10 h-10 rounded-xl",
  lg: "w-12 h-12 rounded-xl",
};

const ICON: Record<NonNullable<LogoMarkProps["size"]>, string> = {
  sm: "w-5 h-5",
  md: "w-6 h-6",
  lg: "w-7 h-7",
};

/**
 * Brand mark inside the rounded green container used across the app.
 * Renders the LogoIcon glyph in `text-primary-foreground` on a `bg-primary`
 * background so it inherits the active theme colours automatically.
 */
export function LogoMark({ size = "md", className }: LogoMarkProps) {
  return (
    <div
      className={cn(
        "bg-primary text-primary-foreground flex items-center justify-center shadow-sm",
        CONTAINER[size],
        className,
      )}
    >
      <LogoIcon className={ICON[size]} />
    </div>
  );
}
