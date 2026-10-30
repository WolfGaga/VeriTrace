/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Bento Light Theme Palette
        bento: {
          bg: '#fafafa',
          surface: '#ffffff',
          surfaceHover: '#f5f5f7',
          border: '#d2d2d7',
          borderLight: '#e8e8ed',
        },
        // Text Colors
        text: {
          primary: '#1d1d1f',
          secondary: '#86868b',
          tertiary: '#6e6e73',
          muted: '#aeaeb2',
        },
        // Accent Colors
        accent: {
          blue: '#0071e3',
          blueHover: '#0077ed',
          purple: '#bf5af2',
          pink: '#ff375f',
          orange: '#ff9f0a',
          green: '#30d158',
          teal: '#64d2ff',
        },
      },
      fontFamily: {
        sans: [
          'SF Pro Display',
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'Roboto',
          'sans-serif',
        ],
        mono: [
          'SF Mono',
          'JetBrains Mono',
          'Fira Code',
          'monospace',
        ],
      },
      fontSize: {
        'display': ['80px', { lineHeight: '84px', letterSpacing: '-0.03em', fontWeight: '700' }],
        'h1': ['56px', { lineHeight: '60px', letterSpacing: '-0.02em', fontWeight: '600' }],
        'h2': ['40px', { lineHeight: '44px', letterSpacing: '-0.01em', fontWeight: '600' }],
        'h3': ['28px', { lineHeight: '32px', letterSpacing: '0', fontWeight: '600' }],
        'h4': ['21px', { lineHeight: '26px', letterSpacing: '0', fontWeight: '600' }],
        'body': ['17px', { lineHeight: '26px', letterSpacing: '0', fontWeight: '400' }],
        'body-sm': ['15px', { lineHeight: '22px', letterSpacing: '0', fontWeight: '400' }],
        'caption': ['14px', { lineHeight: '20px', letterSpacing: '0', fontWeight: '400' }],
        'mono': ['14px', { lineHeight: '22px', letterSpacing: '0', fontWeight: '400' }],
      },
      borderRadius: {
        'bento': '20px',
        'bento-sm': '12px',
        'bento-lg': '28px',
        'bento-xl': '36px',
      },
      boxShadow: {
        'bento': '0 2px 20px 0 rgba(0, 0, 0, 0.04)',
        'bento-hover': '0 8px 40px 0 rgba(0, 0, 0, 0.08)',
        'bento-lg': '0 12px 60px 0 rgba(0, 0, 0, 0.12)',
        'bento-3d': '0 20px 80px -20px rgba(0, 0, 0, 0.25)',
        'bento-inset': 'inset 0 1px 0 0 rgba(255, 255, 255, 0.5)',
      },
      spacing: {
        '18': '4.5rem',
        '88': '22rem',
        '128': '32rem',
      },
      maxWidth: {
        'bento': '1440px',
        'chat': '900px',
      },
      animation: {
        'float': 'float 6s ease-in-out infinite',
        'float-slow': 'float 8s ease-in-out infinite',
        'float-slower': 'float 10s ease-in-out infinite',
        'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'gradient': 'gradient 8s linear infinite',
        'fade-in': 'fadeIn 0.5s ease-out forwards',
        'fade-in-up': 'fadeInUp 0.5s ease-out forwards',
        'slide-in-right': 'slideInRight 0.4s ease-out forwards',
        'scale-in': 'scaleIn 0.3s ease-out forwards',
        'spin-slow': 'spin 8s linear infinite',
      },
      keyframes: {
        float: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-20px)' },
        },
        gradient: {
          '0%, 100%': { backgroundPosition: '0% 50%' },
          '50%': { backgroundPosition: '100% 50%' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        fadeInUp: {
          '0%': { opacity: '0', transform: 'translateY(20px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideInRight: {
          '0%': { opacity: '0', transform: 'translateX(20px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        scaleIn: {
          '0%': { opacity: '0', transform: 'scale(0.95)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
      },
      backdropBlur: {
        'xs': '2px',
      },
      transitionDuration: {
        '400': '400ms',
      },
      transitionTimingFunction: {
        'spring': 'cubic-bezier(0.34, 1.56, 0.64, 1)',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
