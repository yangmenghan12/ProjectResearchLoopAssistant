import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Loop Engineering",
  description: "工程调研 Loop Assistant",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="shell">
          <header className="topbar">
            <div className="topbar__inner">
              <Link className="brand" href="/projects">
                <span className="brand-mark">
                  <img src="/brand/atguigu-mark.png" alt="" />
                </span>
                <span>
                  <strong>Loop Engineering</strong>
                  <small>尚硅谷</small>
                </span>
              </Link>
            </div>
          </header>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
