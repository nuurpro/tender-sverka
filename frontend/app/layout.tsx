import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "Тендер-Сверка — проверка заявок", description: "Требования, заявки и проверяемые источники в одной матрице." };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="ru"><body>{children}</body></html>;
}
