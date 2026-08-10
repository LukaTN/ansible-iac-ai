import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

type ButtonVariant = 'primary' | 'ghost' | 'danger' | 'icon';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  icon?: ReactNode;
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    'bg-[var(--a1)] text-white font-bold hover:bg-[var(--a1-hover)] hover:-translate-y-px disabled:opacity-40',
  ghost:
    'bg-transparent border border-[var(--border2)] text-[var(--muted)] font-medium hover:border-[var(--a2)] hover:text-[var(--a2)] disabled:opacity-40',
  danger:
    'bg-transparent border border-[var(--border)] text-[var(--muted)] hover:border-[var(--err)] hover:text-[var(--err)]',
  icon:
    'bg-transparent border border-[var(--border)] text-[var(--muted)] hover:text-[var(--a1)] hover:border-[var(--a1)]',
};

const ButtonRoot = forwardRef<HTMLButtonElement, ButtonProps>(function ButtonRoot(
  {
    variant = 'primary',
    icon,
    className,
    children,
    ...props
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-lg px-3.5 py-2 text-sm transition-all cursor-pointer disabled:cursor-not-allowed',
        variant === 'icon' && 'h-7 w-7 rounded-md p-0',
        variantClasses[variant],
        className,
      )}
      {...props}
    >
      {icon}
      {children}
    </button>
  );
});

function Primary(props: Omit<ButtonProps, 'variant'>) {
  return <ButtonRoot variant="primary" {...props} />;
}

const Ghost = forwardRef<HTMLButtonElement, Omit<ButtonProps, 'variant'>>(function Ghost(props, ref) {
  return <ButtonRoot ref={ref} variant="ghost" {...props} />;
});

function Danger(props: Omit<ButtonProps, 'variant'>) {
  return <ButtonRoot variant="danger" {...props} />;
}

function Icon(props: Omit<ButtonProps, 'variant'>) {
  return <ButtonRoot variant="icon" {...props} />;
}

export const Button = Object.assign(ButtonRoot, { Primary, Ghost, Danger, Icon });
