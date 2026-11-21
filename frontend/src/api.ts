import { useQuery } from "@tanstack/react-query";
import type { components } from "./api-schema"; // generated from the backend: npm run gen:api

export type User = components["schemas"]["UserOut"];
export type Store = components["schemas"]["StoreOut"];
export type Mailbox = components["schemas"]["MailboxOut"];
export type MailboxDetail = components["schemas"]["MailboxDetail"];
export type SupportAddress = components["schemas"]["SupportAddressOut"];
export type TicketListItem = components["schemas"]["TicketListItem"];
export type TicketList = components["schemas"]["TicketList"];
export type TicketDetail = components["schemas"]["TicketDetail"];
export type TicketMessage = components["schemas"]["MessageOut"];
export type TicketEvent = components["schemas"]["EventOut"];
export type Dashboard = components["schemas"]["Dashboard"];
export type SlaPolicy = components["schemas"]["SlaPolicyOut"];
export type BusinessHours = components["schemas"]["BusinessHours"];
export type AlertSettings = components["schemas"]["AlertSettings"];

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    detail: string,
  ) {
    super(detail);
  }
}

export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    method,
    headers: { "X-Requested-With": "desk", ...(body !== undefined && { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Please check the form and try again";
    throw new ApiError(res.status, data.code ?? "error", detail);
  }
  return data as T;
}

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => api<User>("/me"), retry: false });
export const useStores = () => useQuery({ queryKey: ["stores"], queryFn: () => api<Store[]>("/stores") });
export const useUsers = (enabled = true) =>
  useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/users"), enabled });
export const useMailboxes = () =>
  useQuery({ queryKey: ["mailboxes"], queryFn: () => api<Mailbox[]>("/mailboxes") });
export const useMailbox = (id: number) =>
  useQuery({ queryKey: ["mailbox", id], queryFn: () => api<MailboxDetail>(`/mailboxes/${id}`) });

export const useTickets = (query: string) =>
  useQuery({
    queryKey: ["tickets", query],
    queryFn: () => api<TicketList>(`/tickets?${query}`),
    refetchInterval: 20000, // the inbox keeps itself fresh
  });
export const useTicket = (id: number) =>
  useQuery({
    queryKey: ["ticket", id],
    queryFn: () => api<TicketDetail>(`/tickets/${id}`),
    refetchInterval: 15000,
  });
export const useDashboard = () =>
  useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("/dashboard"), refetchInterval: 30000 });
