import { Menu, LogOut, Settings, User } from "lucide-react";
import { useAppStore } from "@/store/useAppStore";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { useNavigate, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import { clearUserProfile, getUserInitials } from "@/lib/userProfile";

export function TopNav() {
  const { toggleSidebar, setAuthenticated } = useAppStore();
  const isAuthenticated = useAppStore((s) => s.isAuthenticated);
  const navigate = useNavigate();
  const location = useLocation();
  const [initials, setInitials] = useState(() => getUserInitials());

  // Refresh on route change AND on auth-state change so the avatar in the
  // top right reflects the freshly-logged-in user's initials immediately
  // (before this dependency, signing in on /login → navigating to / picked
  // up the new initials only because the route changed; toggling auth on
  // an already-mounted layout left a stale avatar). Cross-tab updates from
  // a `storage` event also force a refresh.
  useEffect(() => {
    setInitials(getUserInitials());
  }, [location.pathname, isAuthenticated]);

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === "pocketwatch_user_profile" || e.key === null) {
        setInitials(getUserInitials());
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const pageTitle = location.pathname === "/chat" ? "Chat" : "Dashboard";

  return (
    <header className="h-14 border-b border-border bg-background flex items-center justify-between px-4 shrink-0">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={toggleSidebar} className="h-8 w-8">
          <Menu className="h-4 w-4" />
        </Button>
        <h1 className="text-lg font-semibold">{pageTitle}</h1>
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant={location.pathname === "/" ? "secondary" : "ghost"}
          size="sm"
          onClick={() => navigate("/")}
        >
          Dashboard
        </Button>
        <Button
          variant={location.pathname === "/chat" ? "secondary" : "ghost"}
          size="sm"
          onClick={() => navigate("/chat")}
        >
          Chat
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" className="h-8 w-8 rounded-full p-0 ml-2">
              <Avatar className="h-8 w-8">
                <AvatarFallback className="bg-primary text-primary-foreground text-xs font-semibold">
                  {initials}
                </AvatarFallback>
              </Avatar>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuItem>
              <User className="mr-2 h-4 w-4" />
              Profile
            </DropdownMenuItem>
            <DropdownMenuItem>
              <Settings className="mr-2 h-4 w-4" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => {
                clearUserProfile();
                setAuthenticated(false);
                navigate("/login");
              }}
            >
              <LogOut className="mr-2 h-4 w-4" />
              Logout
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
