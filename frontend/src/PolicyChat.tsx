import { ActionIcon, Group, Loader, Paper, ScrollArea, Stack, Text, Textarea } from "@mantine/core";
import { IconMessageChatbot, IconSend, IconX } from "@tabler/icons-react";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "./api";

type Source = { id: number; title: string };
type Msg = { role: "user" | "assistant"; text: string; sources?: Source[] };

/** Floating policy assistant: ask free-text questions, answered from the knowledge base. */
export default function PolicyChat() {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const viewport = useRef<HTMLDivElement>(null);

  const ask = useMutation({
    mutationFn: (question: string) =>
      api<{ answer: string; sources: Source[] }>("/kb/ask", "POST", { question }),
    onSuccess: (r) => setMessages((m) => [...m, { role: "assistant", text: r.answer, sources: r.sources }]),
    onError: (e: Error) => setMessages((m) => [...m, { role: "assistant", text: `⚠ ${e.message}` }]),
  });

  useEffect(() => {
    viewport.current?.scrollTo({ top: viewport.current.scrollHeight, behavior: "smooth" });
  }, [messages, ask.isPending]);

  const send = () => {
    const q = input.trim();
    if (!q || ask.isPending) return;
    setMessages((m) => [...m, { role: "user", text: q }]);
    setInput("");
    ask.mutate(q);
  };

  if (!open) {
    return (
      <ActionIcon
        size={56}
        radius="xl"
        variant="filled"
        onClick={() => setOpen(true)}
        aria-label="Ask the policy assistant"
        style={{ position: "fixed", right: 24, bottom: 24, zIndex: 200, boxShadow: "var(--mantine-shadow-lg)" }}
      >
        <IconMessageChatbot size={28} />
      </ActionIcon>
    );
  }

  return (
    <Paper
      withBorder
      shadow="lg"
      radius="md"
      style={{
        position: "fixed",
        right: 24,
        bottom: 24,
        zIndex: 200,
        width: 380,
        maxWidth: "calc(100vw - 48px)",
        height: 520,
        maxHeight: "calc(100vh - 48px)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <Group justify="space-between" p="sm" style={{ borderBottom: "1px solid var(--mantine-color-default-border)" }}>
        <Group gap={8}>
          <IconMessageChatbot size={20} />
          <Text fw={600} size="sm">
            Policy assistant
          </Text>
        </Group>
        <ActionIcon variant="subtle" color="gray" onClick={() => setOpen(false)} aria-label="Close">
          <IconX size={18} />
        </ActionIcon>
      </Group>

      <ScrollArea viewportRef={viewport} style={{ flex: 1 }} p="sm">
        <Stack gap="sm">
          {messages.length === 0 && (
            <Text c="dimmed" size="sm">
              Ask about returns, warranty, shipping or any policy on file — I answer from the knowledge base.
            </Text>
          )}
          {messages.map((m, i) => (
            <Paper
              key={i}
              p="xs"
              radius="md"
              bg={m.role === "user" ? "var(--mantine-primary-color-light)" : "var(--mantine-color-default-hover)"}
              style={{ alignSelf: m.role === "user" ? "flex-end" : "flex-start", maxWidth: "90%" }}
            >
              <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
                {m.text}
              </Text>
              {m.sources && m.sources.length > 0 && (
                <Text c="dimmed" size="xs" mt={6}>
                  Sources: {m.sources.map((s) => s.title).join(", ")}
                </Text>
              )}
            </Paper>
          ))}
          {ask.isPending && (
            <Group gap={6}>
              <Loader size="xs" />
              <Text c="dimmed" size="sm">
                Looking through the policies…
              </Text>
            </Group>
          )}
        </Stack>
      </ScrollArea>

      <Group gap={6} p="sm" align="flex-end" style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
        <Textarea
          style={{ flex: 1 }}
          placeholder="Ask a policy question…"
          autosize
          minRows={1}
          maxRows={4}
          value={input}
          onChange={(e) => setInput(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <ActionIcon size={36} variant="filled" onClick={send} disabled={!input.trim() || ask.isPending} aria-label="Send">
          <IconSend size={18} />
        </ActionIcon>
      </Group>
    </Paper>
  );
}
