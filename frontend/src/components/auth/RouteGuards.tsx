import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAppStore } from "@/store/useAppStore";

/**
 * Wraps routes that require an authenticated user. Anonymous visitors are
 * redirected to /login and the originally-requested location is preserved in
 * `location.state.from` so we can send them back after a successful sign-in.
 */
export function ProtectedRoute() {
  const isAuthenticated = useAppStore((s) => s.isAuthenticated);
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <Outlet />;
}

/**
 * Wraps the auth pages themselves. If the visitor is already signed in we
 * skip the form entirely and send them to the dashboard (or back to wherever
 * they were trying to go before being bounced through /login).
 */
export function GuestRoute() {
  const isAuthenticated = useAppStore((s) => s.isAuthenticated);
  const location = useLocation();

  if (isAuthenticated) {
    const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname;
    return <Navigate to={from && from !== "/login" && from !== "/signup" ? from : "/"} replace />;
  }
  return <Outlet />;
}
