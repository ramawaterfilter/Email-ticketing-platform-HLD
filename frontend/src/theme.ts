import { Card, createTheme, Paper, type MantineColorsTuple } from "@mantine/core";

// Brand teal — fresh, clean-water feel. Index 6 (#0f9d75) is the primary.
const brand: MantineColorsTuple = [
  "#e7faf3",
  "#cbf0e3",
  "#9fe4cd",
  "#6fd8b6",
  "#49cda3",
  "#2fc698",
  "#0f9d75",
  "#0b8a67",
  "#076e52",
  "#02503b",
];

const fontStack =
  'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

export const theme = createTheme({
  primaryColor: "brand",
  primaryShade: { light: 6, dark: 5 },
  colors: { brand },
  defaultRadius: "md",
  fontFamily: fontStack,
  fontFamilyMonospace: 'ui-monospace, SFMono-Regular, Menlo, Monaco, "Cascadia Mono", monospace',
  headings: {
    fontFamily: fontStack,
    fontWeight: "650",
    sizes: {
      h1: { fontSize: "1.75rem", lineHeight: "1.25" },
      h3: { fontSize: "1.3rem", lineHeight: "1.3" },
    },
  },
  cursorType: "pointer",
  components: {
    Card: Card.extend({
      defaultProps: { radius: "lg", padding: "lg", withBorder: true },
      // Lift cards off the page: in dark mode use a surface one step above the page.
      styles: {
        root: { backgroundColor: "light-dark(var(--mantine-color-white), var(--mantine-color-dark-6))" },
      },
    }),
    Paper: Paper.extend({ defaultProps: { radius: "lg" } }),
  },
});
