import { motion, HTMLMotionProps } from 'framer-motion';
import { ReactNode } from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: (string | undefined | null | false)[]) {
  return twMerge(clsx(inputs));
}

type BentoCardVariant = 'default' | 'elevated' | 'gradient' | '3d' | 'glass';
type BentoCardSize = 'sm' | 'md' | 'lg' | 'xl' | 'auto';

interface BentoCardProps extends Omit<HTMLMotionProps<'div'>, 'children'> {
  children: ReactNode;
  variant?: BentoCardVariant;
  size?: BentoCardSize;
  hover?: boolean;
  className?: string;
  padding?: 'none' | 'sm' | 'md' | 'lg';
  noBorder?: boolean;
}

const variantStyles: Record<BentoCardVariant, string> = {
  default: 'bg-bento-surface border-bento-borderLight shadow-bento',
  elevated: 'bg-bento-surface border-bento-borderLight shadow-bento-lg',
  gradient: 'bg-gradient-to-br from-white to-bento-surfaceHover border-bento-borderLight shadow-bento',
  '3d': 'bg-bento-surface border-bento-borderLight shadow-bento-3d',
  glass: 'bg-white/70 backdrop-blur-xl border-white/50 shadow-bento',
};

const sizeStyles: Record<BentoCardSize, string> = {
  sm: 'min-h-[200px]',
  md: 'min-h-[300px]',
  lg: 'min-h-[400px]',
  xl: 'min-h-[500px]',
  auto: '',
};

const paddingStyles = {
  none: 'p-0',
  sm: 'p-4',
  md: 'p-6',
  lg: 'p-8',
};

const hoverAnimation = {
  rest: {
    y: 0,
    scale: 1,
    boxShadow: '0 2px 20px 0 rgba(0, 0, 0, 0.04)',
  },
  hover: {
    y: -4,
    scale: 1.01,
    boxShadow: '0 12px 40px 0 rgba(0, 0, 0, 0.1)',
    transition: {
      duration: 0.3,
      ease: [0.4, 0, 0.2, 1],
    },
  },
};

export function BentoCard({
  children,
  variant = 'default',
  size = 'auto',
  hover = false,
  className,
  padding = 'md',
  noBorder = false,
  ...motionProps
}: BentoCardProps) {
  const baseClasses = cn(
    'rounded-bento overflow-hidden',
    'transition-colors duration-300',
    variantStyles[variant],
    sizeStyles[size],
    paddingStyles[padding],
    noBorder && 'border-0',
    className
  );

  if (hover) {
    return (
      <motion.div
        className={baseClasses}
        initial="rest"
        whileHover="hover"
        variants={hoverAnimation}
        {...motionProps}
      >
        {children}
      </motion.div>
    );
  }

  return (
    <motion.div
      className={baseClasses}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.4, 0, 0.2, 1] }}
      {...motionProps}
    >
      {children}
    </motion.div>
  );
}

export function BentoChatCard({
  children,
  className,
  ...props
}: BentoCardProps) {
  return (
    <BentoCard
      variant="elevated"
      className={cn('flex flex-col', className)}
      padding="none"
      {...props}
    >
      {children}
    </BentoCard>
  );
}

export function BentoSidebarCard({
  children,
  title,
  icon,
  className,
  isCollapsed,
  onToggle,
  ...props
}: BentoCardProps & {
  title?: string;
  icon?: ReactNode;
  isCollapsed?: boolean;
  onToggle?: () => void;
}) {
  return (
    <BentoCard
      variant="default"
      hover
      className={cn('cursor-default', className)}
      padding="none"
      {...props}
    >
      {title && (
        <div
          className="flex items-center justify-between px-5 py-4 border-b border-bento-borderLight cursor-pointer"
          onClick={onToggle}
        >
          <div className="flex items-center gap-2">
            {icon && <span className="text-accent-blue">{icon}</span>}
            <h3 className="text-h4 text-text-primary">{title}</h3>
          </div>
          <motion.button
            className="p-1 rounded-lg hover:bg-bento-surfaceHover transition-colors"
            animate={{ rotate: isCollapsed ? 0 : 180 }}
            transition={{ duration: 0.2 }}
          >
            <svg
              width="20"
              height="20"
              viewBox="0 0 20 20"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M5 7.5L10 12.5L15 7.5"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </motion.button>
        </div>
      )}
      <motion.div
        className="overflow-hidden"
        animate={{
          height: isCollapsed ? 0 : 'auto',
          opacity: isCollapsed ? 0 : 1,
        }}
        transition={{ duration: 0.3, ease: [0.4, 0, 0.2, 1] }}
      >
        <div className="p-5">{children}</div>
      </motion.div>
    </BentoCard>
  );
}

export function BentoInputCard({
  children,
  className,
  isFocused,
  ...props
}: BentoCardProps & { isFocused?: boolean }) {
  return (
    <motion.div
      className={cn(
        'relative rounded-bento-lg overflow-hidden',
        'bg-bento-surface border border-bento-borderLight',
        'transition-all duration-300',
        isFocused && 'shadow-bento-lg border-accent-blue/30',
        !isFocused && 'shadow-bento-hover',
        className
      )}
      animate={{
        y: isFocused ? -2 : 0,
      }}
      transition={{ duration: 0.2 }}
      {...props}
    >
      {isFocused && (
        <motion.div
          className="absolute inset-0 rounded-bento-lg pointer-events-none"
          style={{
            background: 'linear-gradient(135deg, rgba(0, 113, 227, 0.1), rgba(191, 90, 242, 0.1))',
          }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        />
      )}
      <div className="relative z-10 p-4">{children}</div>
    </motion.div>
  );
}

export default BentoCard;
