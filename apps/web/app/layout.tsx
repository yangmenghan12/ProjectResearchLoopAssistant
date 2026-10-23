import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "个人调研助手",
  description: "基于源码证据的项目问答与调研报告",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="shell">
          <header className="topbar">
            <div className="topbar__inner">
              <Link className="brand" href="/assistant">
                <span className="brand-mark">
                  <img src="/brand/atguigu-mark.png" alt="" />
                </span>
                <span>
                  <strong>个人调研助手</strong>
                  <small>项目问答与调研报告</small>
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
