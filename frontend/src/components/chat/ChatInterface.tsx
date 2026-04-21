import { useState, useRef, useEffect } from "react";
import { Send } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useAppStore } from "@/store/useAppStore";
import { cn } from "@/lib/utils";
import { askFinanceQuestion, type ChatHistoryMessage } from "@/lib/api";

const generateId = () => Math.random().toString(36).substring(2, 15);

export function ChatInterface() {
  const { conversations, activeConversationId, addMessage, createConversation } = useAppStore();
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const activeConversation = conversations.find((c) => c.id === activeConversationId);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [activeConversation?.messages.length]);

  const handleSend = async () => {
    const question = input.trim();
    if (!question) return;

    let convId = activeConversationId;
    if (!convId) {
      convId = createConversation();
    }

    const priorMessages = activeConversation?.messages ?? [];
    const history: ChatHistoryMessage[] = priorMessages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }));

    addMessage(convId, {
      id: generateId(),
      role: "user",
      content: question,
      timestamp: new Date(),
    });
    setInput("");
    setIsTyping(true);

    try {
      const sessionId = localStorage.getItem("pocketwatch_session_id") || undefined;
      const response = await askFinanceQuestion(question, sessionId, history);
      addMessage(convId!, {
        id: generateId(),
        role: "assistant",
        content: response.answer,
        timestamp: new Date(),
      });
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Unable to connect to assistant backend.";
      addMessage(convId!, {
        id: generateId(),
        role: "assistant",
        content: `Error: ${errorMessage}`,
        timestamp: new Date(),
      });
    } finally {
      setIsTyping(false);
    }
  };

  const hasUploadedSession = Boolean(localStorage.getItem("pocketwatch_session_id"));

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <ScrollArea className="flex-1 px-4 py-4" ref={scrollRef}>
        <div className="max-w-3xl mx-auto space-y-4">
          {!activeConversation?.messages.length && (
            <div className="text-center py-20 animate-fade-in">
              <div className="w-16 h-16 rounded-2xl bg-accent flex items-center justify-center mx-auto mb-4">
                <span className="text-2xl">💰</span>
              </div>
              <h2 className="text-xl font-semibold mb-2">How can I help with your finances?</h2>
              <p className="text-muted-foreground text-sm max-w-md mx-auto">
                {hasUploadedSession
                  ? "Ask about your uploaded statement (totals, categories, trends) or any general personal finance topic."
                  : "Upload a statement on the dashboard for answers tied to your file, or ask general questions about budgeting, saving, and investing."}
              </p>
            </div>
          )}

          {activeConversation?.messages.map((msg, i) => (
            <div
              key={msg.id}
              className={cn(
                "flex animate-fade-in",
                msg.role === "user" ? "justify-end" : "justify-start"
              )}
              style={{ animationDelay: `${i * 50}ms` }}
            >
              <div
                className={cn(
                  "max-w-[75%] px-4 py-3 rounded-2xl text-sm leading-relaxed",
                  msg.role === "user"
                    ? "chat-bubble-user rounded-br-md whitespace-pre-wrap"
                    : "chat-bubble-ai rounded-bl-md"
                )}
              >
                {msg.role === "assistant" ? (
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      p: ({ children }) => (
                        <p className="mb-2 last:mb-0 whitespace-pre-wrap">{children}</p>
                      ),
                      strong: ({ children }) => (
                        <strong className="font-semibold">{children}</strong>
                      ),
                      em: ({ children }) => <em className="italic">{children}</em>,
                      ul: ({ children }) => (
                        <ul className="list-disc pl-5 my-2 space-y-1">{children}</ul>
                      ),
                      ol: ({ children }) => (
                        <ol className="list-decimal pl-5 my-2 space-y-1">{children}</ol>
                      ),
                      li: ({ children }) => <li className="leading-relaxed">{children}</li>,
                      h1: ({ children }) => (
                        <h1 className="text-base font-semibold mt-1 mb-2">{children}</h1>
                      ),
                      h2: ({ children }) => (
                        <h2 className="text-sm font-semibold mt-1 mb-2">{children}</h2>
                      ),
                      h3: ({ children }) => (
                        <h3 className="text-sm font-semibold mt-1 mb-1">{children}</h3>
                      ),
                      a: ({ children, href }) => (
                        <a
                          href={href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="underline text-primary break-all"
                        >
                          {children}
                        </a>
                      ),
                      code: ({ className, children, ...props }) => {
                        const isInline = !className;
                        if (isInline) {
                          return (
                            <code
                              className="px-1 py-0.5 rounded bg-muted text-foreground/90 text-[0.85em]"
                              {...props}
                            >
                              {children}
                            </code>
                          );
                        }
                        return (
                          <code className="block text-[0.85em]" {...props}>
                            {children}
                          </code>
                        );
                      },
                      pre: ({ children }) => (
                        <pre className="my-2 p-3 rounded-lg bg-muted overflow-x-auto text-xs">
                          {children}
                        </pre>
                      ),
                      blockquote: ({ children }) => (
                        <blockquote className="border-l-2 border-muted-foreground/30 pl-3 italic my-2">
                          {children}
                        </blockquote>
                      ),
                      table: ({ children }) => (
                        <div className="overflow-x-auto my-2">
                          <table className="min-w-full text-xs border-collapse">{children}</table>
                        </div>
                      ),
                      th: ({ children }) => (
                        <th className="border border-border px-2 py-1 text-left font-semibold">
                          {children}
                        </th>
                      ),
                      td: ({ children }) => (
                        <td className="border border-border px-2 py-1 align-top">{children}</td>
                      ),
                      hr: () => <hr className="my-3 border-border" />,
                    }}
                  >
                    {msg.content}
                  </ReactMarkdown>
                ) : (
                  msg.content
                )}
              </div>
            </div>
          ))}

          {isTyping && (
            <div className="flex justify-start animate-fade-in">
              <div className="chat-bubble-ai px-4 py-3 rounded-2xl rounded-bl-md">
                <div className="flex gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-muted-foreground/40 animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="w-2 h-2 rounded-full bg-muted-foreground/40 animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="w-2 h-2 rounded-full bg-muted-foreground/40 animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
              </div>
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Input */}
      <div className="border-t border-border p-4 bg-background">
        <div className="max-w-3xl mx-auto flex items-center gap-2">
          <div className="flex-1 relative">
            <input
              type="text"
              className="w-full px-4 py-2.5 rounded-xl border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring/20 transition-all"
              placeholder={
                hasUploadedSession
                  ? "Ask about your uploaded statement or any finance topic..."
                  : "Ask about your finances..."
              }
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
            />
          </div>
          <Button
            size="icon"
            className="shrink-0 h-9 w-9 rounded-xl"
            onClick={handleSend}
            disabled={!input.trim()}
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
