import {
  ActionIcon,
  Alert,
  AppShell,
  Avatar,
  Badge,
  Box,
  Center,
  Group,
  Loader,
  Menu,
  NavLink,
  Text,
  ThemeIcon,
  useComputedColorScheme,
  useMantineColorScheme,
} from "@mantine/core";
import {
  IconBook2,
  IconBuildingStore,
  IconChartBar,
  IconDroplet,
  IconGauge,
  IconInbox,
  IconLayoutKanban,
  IconLogout,
  IconMail,
  IconMessages,
  IconMoon,
  IconSparkles,
  IconSun,
  IconUsers,
  type Icon,
} from "@tabler/icons-react";
import { Link, Navigate, Outlet, Route, Routes, useLocation } from "react-router";

import { api, ApiError, useMe, type User } from "./api";
import Board from "./pages/Board";
import Ai from "./pages/Ai";
import Canned from "./pages/Canned";
import Emails from "./pages/Emails";
import Inbox from "./pages/Inbox";
import Kb from "./pages/Kb";
import Login from "./pages/Login";
import Reports from "./pages/Reports";
import Sla from "./pages/Sla";
import Stores from "./pages/Stores";
import TicketView from "./pages/TicketView";
import Users from "./pages/Users";

type Role = User["role"];
type NavItem = { to: string; label: string; icon: Icon; roles: Role[] };
type NavSection = { title: string; items: NavItem[] };

const ALL: Role[] = ["admin", "manager", "executive"];
const STAFF: Role[] = ["admin", "manager"];
const ADMIN: Role[] = ["admin"];

const SECTIONS: NavSection[] = [
  {
    title: "Workspace",
    items: [
      { to: "/", label: "Tickets", icon: IconInbox, roles: ALL },
      { to: "/board", label: "Board", icon: IconLayoutKanban, roles: STAFF },
      { to: "/reports", label: "Reports", icon: IconChartBar, roles: STAFF },
    ],
  },
  {
    title: "Admin",
    items: [
      { to: "/admin/emails", label: "Email accounts", icon: IconMail, roles: ADMIN },
      { to: "/admin/stores", label: "Stores", icon: IconBuildingStore, roles: ADMIN },
      { to: "/admin/kb", label: "Knowledge base", icon: IconBook2, roles: ADMIN },
      { to: "/admin/canned", label: "Canned responses", icon: IconMessages, roles: ADMIN },
      { to: "/admin/ai", label: "AI assistant", icon: IconSparkles, roles: ADMIN },
      { to: "/admin/users", label: "People", icon: IconUsers, roles: ADMIN },
      { to: "/admin/sla", label: "SLA and alerts", icon: IconGauge, roles: ADMIN },
    ],
  },
];

function initials(user: User): string {
  const base = (user.name || user.email).trim();
  const parts = base.split(/[\s@._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

function Brand() {
  return (
    <Group gap="sm">
      <ThemeIcon size={34} radius="md" variant="gradient" gradient={{ from: "brand.5", to: "brand.7", deg: 135 }}>
        <IconDroplet size={20} />
      </ThemeIcon>
      <Box>
        <Text fw={700} lh={1.1} size="sm">
          Rama
        </Text>
        <Text c="dimmed" size="xs" lh={1.1}>
          Support Desk
        </Text>
      </Box>
    </Group>
  );
}

function ColorSchemeToggle() {
  const { setColorScheme } = useMantineColorScheme();
  const scheme = useComputedColorScheme("light");
  const next = scheme === "dark" ? "light" : "dark";
  return (
    <ActionIcon
      variant="default"
      size="lg"
      radius="md"
      aria-label="Toggle colour scheme"
      onClick={() => setColorScheme(next)}
    >
      {scheme === "dark" ? <IconSun size={18} /> : <IconMoon size={18} />}
    </ActionIcon>
  );
}

function UserMenu({ user }: { user: User }) {
  const signOut = async () => {
    await api("/auth/logout", "POST");
    window.location.href = "/login"; // full reload drops every cached query
  };
  const roleColor = user.role === "admin" ? "brand" : user.role === "manager" ? "blue" : "gray";
  return (
    <Menu position="bottom-end" width={220} withArrow shadow="md">
      <Menu.Target>
        <Group gap="xs" style={{ cursor: "pointer" }}>
          <Avatar color="brand" radius="xl" size={34} variant="filled">
            {initials(user)}
          </Avatar>
        </Group>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>
          <Text size="sm" fw={600} truncate>
            {user.name || user.email}
          </Text>
          <Group gap={6} mt={4}>
            <Badge size="xs" variant="light" color={roleColor}>
              {user.role}
            </Badge>
            <Text size="xs" c="dimmed" truncate>
              {user.email}
            </Text>
          </Group>
        </Menu.Label>
        <Menu.Divider />
        <Menu.Item leftSection={<IconLogout size={16} />} onClick={signOut}>
          Sign out
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}

function Shell({ user }: { user: User }) {
  const { pathname } = useLocation();
  const active = (to: string) => (to === "/" ? pathname === "/" : pathname.startsWith(to));

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 248, breakpoint: "sm" }}
      padding="lg"
      styles={{
        main: { backgroundColor: "light-dark(var(--mantine-color-gray-0), var(--mantine-color-dark-9))" },
        navbar: { backgroundColor: "var(--mantine-color-body)" },
      }}
    >
      <AppShell.Header>
        <Group h="100%" px="lg" justify="space-between">
          <Brand />
          <Group gap="sm">
            <ColorSchemeToggle />
            <UserMenu user={user} />
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="md">
        {SECTIONS.map((section) => {
          const items = section.items.filter((i) => i.roles.includes(user.role));
          if (items.length === 0) return null;
          return (
            <Box key={section.title} mb="md">
              <Text size="xs" fw={700} c="dimmed" tt="uppercase" px="sm" mb={6} style={{ letterSpacing: 0.5 }}>
                {section.title}
              </Text>
              {items.map((item) => {
                const isActive = active(item.to);
                return (
                  <NavLink
                    key={item.to}
                    component={Link}
                    to={item.to}
                    label={item.label}
                    leftSection={<item.icon size={19} stroke={1.7} />}
                    styles={{
                      root: {
                        borderRadius: "var(--mantine-radius-md)",
                        marginBottom: 2,
                        ...(isActive && {
                          backgroundColor:
                            "light-dark(var(--mantine-color-brand-0), color-mix(in srgb, var(--mantine-color-brand-6) 22%, transparent))",
                          color: "light-dark(var(--mantine-color-brand-8), var(--mantine-color-brand-2))",
                        }),
                      },
                      label: isActive ? { fontWeight: 600 } : undefined,
                    }}
                  />
                );
              })}
            </Box>
          );
        })}
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
        <Loader color="brand" />
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
        {(user?.role === "manager" || user?.role === "admin") && (
          <>
            <Route path="board" element={<Board />} />
            <Route path="reports" element={<Reports />} />
          </>
        )}
        {user?.role === "admin" && (
          <>
            <Route path="admin/emails" element={<Emails />} />
            <Route path="admin/stores" element={<Stores />} />
            <Route path="admin/kb" element={<Kb />} />
            <Route path="admin/canned" element={<Canned />} />
            <Route path="admin/ai" element={<Ai />} />
            <Route path="admin/users" element={<Users />} />
            <Route path="admin/sla" element={<Sla />} />
          </>
        )}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
