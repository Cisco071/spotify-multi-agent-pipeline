import "./globals.css";

export const metadata = {
  title: "Spotify Review Insights",
  description: "Multi-agent analysis of Spotify Google Play reviews: rankings, evidence and AI recommendation.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
