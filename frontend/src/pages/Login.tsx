import { Alert, Button, Center, Divider, Paper, Stack, Text, TextInput, useComputedColorScheme } from "@mantine/core";
import { IconBrandGoogleFilled } from "@tabler/icons-react";
import { type FormEvent, useState } from "react";
import { useSearchParams } from "react-router";

import { api } from "../api";

// The server only ever sends one of these fixed codes; the page never displays raw URL text.
const MESSAGES: Record<string, string> = {
  google: "Google sign-in did not complete. Please try again.",
  unverified: "Your Google account email is not verified.",
  domain: "Please sign in with your company Google account.",
  not_allowed: "You have not been given access yet. Ask an admin to add you.",
};

export default function Login() {
  const [params] = useSearchParams();
  const scheme = useComputedColorScheme("light");
  const error = params.get("error");
  const [email, setEmail] = useState("");
  const [devError, setDevError] = useState("");

  const devLogin = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api("/auth/dev-login", "POST", { email });
      window.location.href = "/";
    } catch {
      setDevError("Sign-in refused: this email is not an approved, active user.");
    }
  };

  return (
    <Center
      h="100vh"
      style={{ backgroundColor: "light-dark(var(--mantine-color-gray-0), var(--mantine-color-dark-8))" }}
    >
      <Paper withBorder shadow="md" p="xl" w={400} radius="lg">
        <Stack>
          <Stack gap="xs" align="center">
            <img
              src="/logo.png"
              alt="Phoenix Gravity"
              // ponytail: navy mark on transparent bg — flip to white in dark mode so it shows
              style={{ height: 88, display: "block", filter: scheme === "dark" ? "brightness(0) invert(1)" : undefined }}
            />
            <Text c="dimmed" size="sm">
              Sign in to your workspace
            </Text>
          </Stack>

          {error && <Alert color="red">{MESSAGES[error] ?? "Sign-in failed."}</Alert>}

          <Button
            component="a"
            href="/api/v1/auth/google/login"
            size="md"
            leftSection={<IconBrandGoogleFilled size={18} />}
          >
            Sign in with Google
          </Button>

          {import.meta.env.DEV && (
            <form onSubmit={devLogin}>
              <Stack>
                <Divider label="Development only" />
                {devError && <Alert color="red">{devError}</Alert>}
                <TextInput
                  label="Email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.currentTarget.value)}
                />
                <Button type="submit" variant="default">
                  Sign in without Google
                </Button>
              </Stack>
            </form>
          )}
        </Stack>
      </Paper>
    </Center>
  );
}
