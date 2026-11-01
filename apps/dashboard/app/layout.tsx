import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Evolving AI Company | Autonomous Neural Organization (ANCOS)',
  description: 'Self-Learning, Self-Evolving, Autonomous Multi-Agent Engineering Company',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[#060a12] text-slate-100 min-h-screen flex flex-col font-sans antialiased selection:bg-blue-600 selection:text-white">
        {children}
      </body>
    </html>
  );
}
