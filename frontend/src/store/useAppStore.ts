import { create } from "zustand";
import type { Conversation, Message } from "@/types/chat";

interface AppState {
  conversations: Conversation[];
  activeConversationId: string | null;
  sidebarOpen: boolean;
  isAuthenticated: boolean;

  setAuthenticated: (auth: boolean) => void;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  createConversation: () => string;
  setActiveConversation: (id: string) => void;
  addMessage: (conversationId: string, message: Message) => void;
  deleteConversation: (id: string) => void;
  renameConversation: (id: string, title: string) => void;
}

const generateId = () => Math.random().toString(36).substring(2, 15);

export const useAppStore = create<AppState>((set) => ({
  conversations: [],
  activeConversationId: null,
  sidebarOpen: true,
  isAuthenticated: true,

  setAuthenticated: (auth) => set({ isAuthenticated: auth }),
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),

  createConversation: () => {
    const id = generateId();
    const conv: Conversation = {
      id,
      title: "New Chat",
      messages: [],
      createdAt: new Date(),
      updatedAt: new Date(),
    };
    set((s) => ({
      conversations: [conv, ...s.conversations],
      activeConversationId: id,
    }));
    return id;
  },

  setActiveConversation: (id) => set({ activeConversationId: id }),

  addMessage: (conversationId, message) =>
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === conversationId
          ? {
              ...c,
              messages: [...c.messages, message],
              title: c.messages.length === 0 ? message.content.slice(0, 40) + "..." : c.title,
              updatedAt: new Date(),
            }
          : c
      ),
    })),

  deleteConversation: (id) =>
    set((s) => ({
      conversations: s.conversations.filter((c) => c.id !== id),
      activeConversationId: s.activeConversationId === id ? s.conversations[0]?.id ?? null : s.activeConversationId,
    })),

  renameConversation: (id, title) =>
    set((s) => ({
      conversations: s.conversations.map((c) => (c.id === id ? { ...c, title } : c)),
    })),
}));
