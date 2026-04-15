import { Plus, MessageSquare, Trash2, Pencil } from "lucide-react";
import { useAppStore } from "@/store/useAppStore";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { format } from "date-fns";
import { useState } from "react";

export function AppSidebar() {
  const {
    conversations,
    activeConversationId,
    sidebarOpen,
    createConversation,
    setActiveConversation,
    deleteConversation,
    renameConversation,
  } = useAppStore();

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");

  const handleRename = (id: string) => {
    if (editTitle.trim()) {
      renameConversation(id, editTitle.trim());
    }
    setEditingId(null);
  };

  if (!sidebarOpen) return null;

  return (
    <aside className="w-[280px] min-w-[280px] h-screen flex flex-col bg-sidebar border-r border-sidebar-border">
      {/* Header */}
      <div className="p-4 border-b border-sidebar-border">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
            <span className="text-primary-foreground font-bold text-sm">P</span>
          </div>
          <span className="font-semibold text-sidebar-foreground text-lg">PocketWatch</span>
        </div>
        <Button
          onClick={createConversation}
          className="w-full gap-2"
          size="sm"
        >
          <Plus className="h-4 w-4" />
          New Chat
        </Button>
      </div>

      {/* Conversations */}
      <ScrollArea className="flex-1 px-2 py-2">
        <div className="space-y-1">
          {conversations.map((conv, i) => (
            <div
              key={conv.id}
              className={cn(
                "group flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer transition-all duration-200 animate-slide-in-left",
                activeConversationId === conv.id
                  ? "bg-sidebar-accent border border-sidebar-primary/20"
                  : "hover:bg-sidebar-accent/50"
              )}
              style={{ animationDelay: `${i * 50}ms` }}
              onClick={() => setActiveConversation(conv.id)}
            >
              <MessageSquare className="h-4 w-4 text-muted-foreground shrink-0" />
              <div className="flex-1 min-w-0">
                {editingId === conv.id ? (
                  <input
                    className="w-full bg-background text-sm rounded px-1 py-0.5 border border-input"
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    onBlur={() => handleRename(conv.id)}
                    onKeyDown={(e) => e.key === "Enter" && handleRename(conv.id)}
                    autoFocus
                    onClick={(e) => e.stopPropagation()}
                  />
                ) : (
                  <>
                    <p className="text-sm font-medium truncate text-sidebar-foreground">{conv.title}</p>
                    <p className="text-xs text-muted-foreground">{format(conv.updatedAt, "MMM d, yyyy")}</p>
                  </>
                )}
              </div>
              <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  className="p-1 rounded hover:bg-muted"
                  onClick={(e) => {
                    e.stopPropagation();
                    setEditingId(conv.id);
                    setEditTitle(conv.title);
                  }}
                >
                  <Pencil className="h-3 w-3 text-muted-foreground" />
                </button>
                <button
                  className="p-1 rounded hover:bg-destructive/10"
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteConversation(conv.id);
                  }}
                >
                  <Trash2 className="h-3 w-3 text-destructive" />
                </button>
              </div>
            </div>
          ))}
        </div>
      </ScrollArea>
    </aside>
  );
}
