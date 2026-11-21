import { Alert, Button, Center, Divider, Paper, Stack, TextInput, Title } from "@mantine/core";
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
    <Center h="100vh">
      <Paper withBorder p="xl" w={380}>
        <Stack>
          <Title order={3}>Rama Support Desk</Title>
          {error && <Alert color="red">{MESSAGES[error] ?? "Sign-in failed."}</Alert>}
          <Button component="a" href="/api/v1/auth/google/login">
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
