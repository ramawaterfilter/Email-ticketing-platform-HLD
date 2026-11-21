import {
  ActionIcon,
  Alert,
  AppShell,
  Button,
  Center,
  Group,
  Loader,
  NavLink,
  Text,
  Title,
  useComputedColorScheme,
  useMantineColorScheme,
} from "@mantine/core";
import { Link, Navigate, Outlet, Route, Routes, useLocation } from "react-router";

import { api, ApiError, useMe, type User } from "./api";
import Board from "./pages/Board";
import Emails from "./pages/Emails";
import Inbox from "./pages/Inbox";
import Login from "./pages/Login";
import Sla from "./pages/Sla";
import Stores from "./pages/Stores";
import TicketView from "./pages/TicketView";
import Users from "./pages/Users";

function ColorSchemeToggle() {
  const { setColorScheme } = useMantineColorScheme();
  const scheme = useComputedColorScheme("light");
  const next = scheme === "dark" ? "light" : "dark";
  return (
    <ActionIcon variant="default" size="lg" aria-label="Toggle colour scheme" onClick={() => setColorScheme(next)}>
      {scheme === "dark" ? "☀" : "☾"}
    </ActionIcon>
  );
}

function Shell({ user }: { user: User }) {
  const { pathname } = useLocation();
  const links = [
    { to: "/", label: "Tickets" },
    ...(user.role === "manager" || user.role === "admin" ? [{ to: "/board", label: "Board" }] : []),
    ...(user.role === "admin"
      ? [
          { to: "/admin/emails", label: "Email accounts" },
          { to: "/admin/stores", label: "Stores" },
          { to: "/admin/users", label: "People" },
          { to: "/admin/sla", label: "SLA & alerts" },
        ]
      : []),
  ];
  const signOut = async () => {
    await api("/auth/logout", "POST");
    window.location.href = "/login"; // full reload drops every cached query
  };

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 200, breakpoint: "xs" }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Title order={4}>Rama Support Desk</Title>
          <Group gap="sm">
            <Text size="sm" c="dimmed">
              {user.email} · {user.role}
            </Text>
            <ColorSchemeToggle />
            <Button variant="default" size="xs" onClick={signOut}>
              Sign out
            </Button>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="xs">
        {links.map((l) => (
          <NavLink key={l.to} component={Link} to={l.to} label={l.label} active={pathname === l.to} />
        ))}
      </AppShell.Navbar>
      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}

export default function App() {
  const me = useMe();

  if (me.isPending) {
    return (
      <Center h="100vh">
        <Loader />
      </Center>
    );
  }
  // 401 simply means "not signed in". Anything else is a real problem and must not look like a logout.
  if (me.isError && !(me.error instanceof ApiError && me.error.status === 401)) {
    return (
      <Center h="100vh">
        <Alert color="red" title="Cannot reach the server">
          Please check your connection and reload the page.
        </Alert>
      </Center>
    );
  }
  const user = me.data;

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
      <Route element={user ? <Shell user={user} /> : <Navigate to="/login" replace />}>
        <Route index element={<Inbox />} />
        <Route path="tickets/:id" element={<TicketView />} />
        {(user?.role === "manager" || user?.role === "admin") && <Route path="board" element={<Board />} />}
        {user?.role === "admin" && (
          <>
            <Route path="admin/emails" element={<Emails />} />
            <Route path="admin/stores" element={<Stores />} />
            <Route path="admin/users" element={<Users />} />
            <Route path="admin/sla" element={<Sla />} />
          </>
        )}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
