import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  PasswordInput,
  SegmentedControl,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";

type LlmSettings = {
  provider: string | null;
  model: string | null;
  base_url: string | null;
  configured: boolean;
  apply_priority: boolean;
  embedding_model: string | null;
  embedding_base_url: string | null;
  embedding_configured: boolean;
};

// A few common OpenAI-compatible endpoints, shown as hints.
const PRESETS = [
  { label: "OpenAI (ChatGPT)", base: "https://api.openai.com/v1", model: "gpt-4o" },
  { label: "NVIDIA NIM", base: "https://integrate.api.nvidia.com/v1", model: "meta/llama-3.1-70b-instruct" },
  { label: "OpenRouter", base: "https://openrouter.ai/api/v1", model: "openai/gpt-4o" },
  { label: "Groq", base: "https://api.groq.com/openai/v1", model: "llama-3.3-70b-versatile" },
  { label: "Local (Ollama)", base: "http://localhost:11434/v1", model: "llama3.1" },
];

export default function Ai() {
  const queryClient = useQueryClient();
  const current = useQuery({ queryKey: ["llm"], queryFn: () => api<LlmSettings>("/settings/llm") });

  const [provider, setProvider] = useState("anthropic");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [applyPriority, setApplyPriority] = useState(true);
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [embeddingBaseUrl, setEmbeddingBaseUrl] = useState("");
  const [embeddingKey, setEmbeddingKey] = useState("");
  const [testResult, setTestResult] = useState<string | null>(null);

  useEffect(() => {
    if (current.data) {
      setProvider(current.data.provider ?? "anthropic");
      setModel(current.data.model ?? "");
      setBaseUrl(current.data.base_url ?? "");
      setApplyPriority(current.data.apply_priority);
      setEmbeddingModel(current.data.embedding_model ?? "");
      setEmbeddingBaseUrl(current.data.embedding_base_url ?? "");
    }
  }, [current.data]);

  const save = useMutation({
    mutationFn: () =>
      api("/settings/llm", "PUT", {
        provider,
        model: model.trim() || null,
        base_url: provider === "openai" ? baseUrl.trim() || null : null,
        apply_priority: applyPriority,
        embedding_model: embeddingModel.trim(),
        embedding_base_url: embeddingBaseUrl.trim(),
        // Only send keys if the admin typed one, so saving other fields keeps them.
        ...(apiKey ? { api_key: apiKey } : {}),
        ...(embeddingKey ? { embedding_api_key: embeddingKey } : {}),
      }),
    onSuccess: () => {
      setApiKey("");
      setEmbeddingKey("");
      setTestResult(null);
      queryClient.invalidateQueries({ queryKey: ["llm"] });
    },
  });

  const test = useMutation({
    mutationFn: () => api<{ ok: boolean; sample: string }>("/settings/llm/test", "POST"),
    onSuccess: (r) => setTestResult(`✓ Connected. Sample: "${r.sample}"`),
    onError: (e: Error) => setTestResult(`✗ ${e.message}`),
  });

  const configured = current.data?.configured;

  return (
    <Stack maw={640}>
      <Group justify="space-between">
        <Title order={3}>AI assistant</Title>
        {configured ? <Badge color="green">Connected</Badge> : <Badge color="gray">Not configured</Badge>}
      </Group>
      <Text c="dimmed" size="sm">
        The assistant drafts a suggested reply for each customer message, using that store's policies. A human always
        reviews and sends. Connect Claude, or any OpenAI-compatible provider (ChatGPT, NVIDIA, OpenRouter, Groq, a local
        model, …).
      </Text>

      <Card withBorder>
        <Stack>
          <SegmentedControl
            value={provider}
            onChange={setProvider}
            data={[
              { value: "anthropic", label: "Claude (Anthropic)" },
              { value: "openai", label: "OpenAI-compatible" },
            ]}
          />

          {provider === "openai" && (
            <>
              <TextInput
                label="API base URL"
                description="The provider's OpenAI-compatible endpoint"
                placeholder="https://api.openai.com/v1"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.currentTarget.value)}
              />
              <Group gap="xs">
                <Text size="xs" c="dimmed">
                  Presets:
                </Text>
                {PRESETS.map((p) => (
                  <Anchor
                    key={p.label}
                    size="xs"
                    onClick={() => {
                      setBaseUrl(p.base);
                      setModel(p.model);
                    }}
                  >
                    {p.label}
                  </Anchor>
                ))}
              </Group>
            </>
          )}

          <TextInput
            label="Model"
            placeholder={provider === "anthropic" ? "claude-opus-5-5" : "gpt-4o"}
            value={model}
            onChange={(e) => setModel(e.currentTarget.value)}
          />
          <PasswordInput
            label="API key"
            description={configured ? "A key is saved. Leave blank to keep it, or enter a new one." : undefined}
            placeholder={configured ? "•••••••• (saved)" : provider === "anthropic" ? "sk-ant-…" : "sk-…"}
            value={apiKey}
            onChange={(e) => setApiKey(e.currentTarget.value)}
          />

          <Switch
            label="Set ticket priority from the AI classification"
            description="Applies the AI's low/normal/high/urgent rating to new tickets. Never overrides a priority an agent set by hand."
            checked={applyPriority}
            onChange={(e) => setApplyPriority(e.currentTarget.checked)}
          />

          <TextInput
            label="Embedding model (optional)"
            description="Turns on semantic search over the knowledge base, so a large policy library scales — only the most relevant passages go into each draft. Leave blank to inject whole documents. After setting it, run Reindex on the Knowledge base screen."
            placeholder="e.g. text-embedding-3-small, or your provider's embeddings model"
            value={embeddingModel}
            onChange={(e) => setEmbeddingModel(e.currentTarget.value)}
          />
          {embeddingModel.trim() && (
            <>
              <TextInput
                label="Embedding endpoint (optional)"
                description="Leave blank to reuse the endpoint above. Set this to use a different embeddings provider (e.g. OpenAI's https://api.openai.com/v1) while chat stays on the provider above."
                placeholder="https://api.openai.com/v1"
                value={embeddingBaseUrl}
                onChange={(e) => setEmbeddingBaseUrl(e.currentTarget.value)}
              />
              <PasswordInput
                label="Embedding API key (optional)"
                description={
                  current.data?.embedding_configured
                    ? "A separate embedding key is saved. Leave blank to keep it."
                    : "Leave blank to reuse the key above, or enter a key for the embedding endpoint."
                }
                placeholder={current.data?.embedding_configured ? "•••••••• (saved)" : "sk-…"}
                value={embeddingKey}
                onChange={(e) => setEmbeddingKey(e.currentTarget.value)}
              />
            </>
          )}

          {save.isError && <Alert color="red">{(save.error as Error).message}</Alert>}
          {testResult && <Alert color={testResult.startsWith("✓") ? "green" : "red"}>{testResult}</Alert>}

          <Group justify="space-between">
            <Button
              variant="default"
              disabled={!configured}
              loading={test.isPending}
              onClick={() => {
                setTestResult(null);
                test.mutate();
              }}
            >
              Test connection
            </Button>
            <Button loading={save.isPending} onClick={() => save.mutate()}>
              Save
            </Button>
          </Group>
        </Stack>
      </Card>

      <Text size="xs" c="dimmed">
        The API key is stored encrypted and never shown again. Suggestions turn on as soon as a provider is connected;
        clear the key to turn them off.
      </Text>
    </Stack>
  );
}
