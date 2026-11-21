import {
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  MultiSelect,
  NumberInput,
  Select,
  Stack,
  Switch,
  Table,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api, type User, useStores, useUsers } from "../api";

type Draft = Pick<User, "email" | "name" | "role" | "store_ids" | "max_open_tickets" | "is_active"> & { id?: number };
const NEW: Draft = { email: "", name: "", role: "executive", store_ids: [], max_open_tickets: 30, is_active: true };
const ROLES = [
  { value: "executive", label: "Executive" },
  { value: "manager", label: "Manager" },
  { value: "admin", label: "Admin" },
];

export default function Users() {
  const users = useUsers();
  const stores = useStores();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft | null>(null);

  const storeName = new Map(stores.data?.map((s) => [s.id, s.name]));
  const storeOptions = (stores.data ?? []).map((s) => ({ value: String(s.id), label: s.name }));

  const save = useMutation({
    mutationFn: ({ id, email, is_active, ...rest }: Draft) =>
      id
        ? api<User>(`/users/${id}`, "PATCH", { ...rest, is_active })
        : api<User>("/users", "POST", { ...rest, email }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
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
        <Title order={3}>People</Title>
        <Button onClick={() => open(NEW)}>Add person</Button>
      </Group>
      {users.isError && <Alert color="red">Could not load people.</Alert>}

      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Email</Table.Th>
            <Table.Th>Name</Table.Th>
            <Table.Th>Role</Table.Th>
            <Table.Th>Stores</Table.Th>
            <Table.Th>Ticket limit</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {users.data?.map((u) => (
            <Table.Tr key={u.id}>
              <Table.Td>{u.email}</Table.Td>
              <Table.Td>{u.name || "—"}</Table.Td>
              <Table.Td tt="capitalize">{u.role}</Table.Td>
              <Table.Td>
                {u.store_ids.length ? u.store_ids.map((id) => storeName.get(id) ?? id).join(", ") : "All stores"}
              </Table.Td>
              <Table.Td>{u.max_open_tickets}</Table.Td>
              <Table.Td>
                <Badge color={u.is_active ? "green" : "gray"}>{u.is_active ? "Active" : "Inactive"}</Badge>
              </Table.Td>
              <Table.Td ta="right">
                <Button variant="subtle" size="xs" onClick={() => open(u)}>
                  Edit
                </Button>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <Modal opened={draft !== null} onClose={() => setDraft(null)} title={draft?.id ? "Edit person" : "Add person"}>
        {draft && (
          <form onSubmit={submit}>
            <Stack>
              {save.isError && <Alert color="red">{save.error.message}</Alert>}
              <TextInput
                label="Company email"
                description="They sign in with this Google account"
                type="email"
                required
                disabled={!!draft.id}
                value={draft.email}
                onChange={(e) => setDraft({ ...draft, email: e.currentTarget.value })}
              />
              <TextInput
                label="Name"
                maxLength={100}
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.currentTarget.value })}
              />
              <Select
                label="Role"
                data={ROLES}
                allowDeselect={false}
                value={draft.role}
                onChange={(role) => role && setDraft({ ...draft, role: role as User["role"] })}
              />
              <MultiSelect
                label="Stores"
                description="Leave empty to let this person work on all stores"
                data={storeOptions}
                value={draft.store_ids.map(String)}
                onChange={(ids) => setDraft({ ...draft, store_ids: ids.map(Number) })}
              />
              <NumberInput
                label="Maximum open tickets"
                min={1}
                max={500}
                allowDecimal={false}
                value={draft.max_open_tickets}
                onChange={(n) => setDraft({ ...draft, max_open_tickets: Number(n) || 1 })}
              />
              {draft.id && (
                <Switch
                  label="Active (can sign in)"
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
