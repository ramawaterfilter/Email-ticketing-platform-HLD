import { Alert, Badge, Button, Group, Modal, PasswordInput, Stack, Switch, Table, Textarea, TextInput, Title } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api, type Store, useStores } from "../api";

type Draft = {
  id?: number;
  name: string;
  signature_text: string;
  ai_guidance: string;
  is_active: boolean;
  shopify_domain: string;
  shopify_token: string;
  shopify_connected: boolean;
};
const NEW: Draft = {
  name: "",
  signature_text: "",
  ai_guidance: "",
  is_active: true,
  shopify_domain: "",
  shopify_token: "",
  shopify_connected: false,
};

export default function Stores() {
  const stores = useStores();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft | null>(null);

  const save = useMutation({
    mutationFn: ({ id, name, signature_text, ai_guidance, is_active, shopify_domain, shopify_token }: Draft) => {
      // Only send the token when the admin typed one, so saving other fields never clears it.
      const shopify = { shopify_domain, ...(shopify_token ? { shopify_token } : {}) };
      return id
        ? api<Store>(`/stores/${id}`, "PATCH", { name, signature_text, ai_guidance, is_active, ...shopify })
        : api<Store>("/stores", "POST", { name, signature_text, ai_guidance, ...shopify });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["stores"] });
      setDraft(null);
    },
  });
  const open = (d: Draft) => {
    save.reset();
    setDraft(d);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (draft) save.mutate(draft);
  };

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Stores</Title>
        <Button onClick={() => open(NEW)}>Add store</Button>
      </Group>
      {stores.isError && <Alert color="red">Could not load stores.</Alert>}

      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th>Email signature</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {stores.data?.map((s) => (
            <Table.Tr key={s.id}>
              <Table.Td>{s.name}</Table.Td>
              <Table.Td style={{ whiteSpace: "pre-wrap" }}>{s.signature_text || "—"}</Table.Td>
              <Table.Td>
                <Badge color={s.is_active ? "green" : "gray"}>{s.is_active ? "Active" : "Inactive"}</Badge>
              </Table.Td>
              <Table.Td ta="right">
                <Button
                  variant="subtle"
                  size="xs"
                  onClick={() =>
                    open({
                      id: s.id,
                      name: s.name,
                      signature_text: s.signature_text,
                      ai_guidance: s.ai_guidance,
                      is_active: s.is_active,
                      shopify_domain: s.shopify_domain ?? "",
                      shopify_token: "",
                      shopify_connected: s.shopify_connected,
                    })
                  }
                >
                  Edit
                </Button>
              </Table.Td>
            </Table.Tr>
          ))}
          {stores.data?.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={4}>No stores yet. Add your first store.</Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>

      <Modal opened={draft !== null} onClose={() => setDraft(null)} title={draft?.id ? "Edit store" : "Add store"}>
        {draft && (
          <form onSubmit={submit}>
            <Stack>
              {save.isError && <Alert color="red">{save.error.message}</Alert>}
              <TextInput
                label="Store name"
                required
                maxLength={100}
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.currentTarget.value })}
              />
              <Textarea
                label="Email signature"
                description="Added to the end of every reply sent for this store"
                autosize
                minRows={3}
                maxLength={5000}
                value={draft.signature_text}
                onChange={(e) => setDraft({ ...draft, signature_text: e.currentTarget.value })}
              />
              <Textarea
                label="AI reply guidance"
                description="Brand tone and reply rules the AI follows when drafting suggestions for this store"
                autosize
                minRows={3}
                maxLength={20000}
                value={draft.ai_guidance}
                onChange={(e) => setDraft({ ...draft, ai_guidance: e.currentTarget.value })}
              />
              <TextInput
                label="Shopify store domain"
                description="For order lookups in drafts, e.g. my-store.myshopify.com"
                placeholder="my-store.myshopify.com"
                value={draft.shopify_domain}
                onChange={(e) => setDraft({ ...draft, shopify_domain: e.currentTarget.value })}
              />
              <PasswordInput
                label="Shopify Admin API access token"
                description={
                  draft.shopify_connected
                    ? "A token is saved. Leave blank to keep it, or enter a new one to replace it."
                    : "Custom app token with read_orders and read_customers scopes"
                }
                placeholder={draft.shopify_connected ? "•••••••• (saved)" : "shpat_…"}
                value={draft.shopify_token}
                onChange={(e) => setDraft({ ...draft, shopify_token: e.currentTarget.value })}
              />
              {draft.id && (
                <Switch
                  label="Active"
                  checked={draft.is_active}
                  onChange={(e) => setDraft({ ...draft, is_active: e.currentTarget.checked })}
                />
              )}
              <Group justify="flex-end">
                <Button variant="default" onClick={() => setDraft(null)}>
                  Cancel
                </Button>
                <Button type="submit" loading={save.isPending}>
                  Save
                </Button>
              </Group>
            </Stack>
          </form>
        )}
      </Modal>
    </Stack>
  );
}
