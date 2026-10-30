import { BrowserRouter } from "react-router-dom";
import { Suspense, lazy, useState } from "react";
import RouterRoutes from "@/routes/routes";
import { GradientMeshFallback } from "@/components/3d/GradientMesh";
import ErrorBoundary from "@/components/ErrorBoundary";

import "@/styles/globals.css";

const GradientMesh = lazy(() =>
  import("@/components/3d/GradientMesh").then((mod) => ({
    default: mod.GradientMesh,
  }))
);

function Header() {
  const [lang, setLang] = useState<string>(() => {
    return localStorage.getItem('veritrace_lang') || 'en';
  });

  const toggleLang = () => {
    const newLang = lang === 'en' ? 'cn' : 'en';
    setLang(newLang);
    localStorage.setItem('veritrace_lang', newLang);
    window.dispatchEvent(new CustomEvent('veritrace-lang-change', { detail: newLang }));
  };

  return (
    <header className="fixed top-0 left-0 right-0 z-50 glass-effect border-b border-bento-borderLight/50">
      <nav className="max-w-bento mx-auto px-6 h-16 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <svg
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            className="text-accent-blue"
          >
            <path
              d="M12 2L2 7L12 12L22 7L12 2Z"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path
              d="M2 17L12 22L22 17"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path
              d="M2 12L12 17L22 12"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className="text-h4 font-semibold gradient-text">VERITRACE</span>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={toggleLang}
            className="btn-ghost text-caption flex items-center gap-1.5"
            title={lang === 'en' ? 'Switch to Chinese prompts' : '切换为英文提示词'}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" className="mr-0.5">
              <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.2"/>
              <ellipse cx="8" cy="8" rx="3" ry="6.5" stroke="currentColor" strokeWidth="1.2"/>
              <path d="M1.5 8H14.5" stroke="currentColor" strokeWidth="1.2"/>
            </svg>
            {lang === 'en' ? 'EN' : '中文'}
          </button>

          <div className="w-px h-4 bg-bento-border" />

          <button
            onClick={() => {
              window.dispatchEvent(new Event('veritrace-new-chat'));
            }}
            className="btn-ghost text-caption"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
              className="mr-1.5"
            >
              <path
                d="M8 3V13M3 8H13"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
            New Chat
          </button>
        </div>
      </nav>
    </header>
  );
}

function App() {
  const routeBase = import.meta.env.BASE_URL === './'
    ? new URL('.', window.location.href).pathname
    : import.meta.env.BASE_URL;
  return (
    <BrowserRouter basename={routeBase}>
      <div className="min-h-screen bg-bento-bg">
        <ErrorBoundary fallback={<GradientMeshFallback />}>
          <Suspense fallback={<GradientMeshFallback />}>
            <GradientMesh />
          </Suspense>
        </ErrorBoundary>

        <Header />

        <main className="pt-16 min-h-screen">
          <RouterRoutes />
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;
