import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'icon';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  icon?: ReactNode;
}

const variantClass: Record<ButtonVariant, string> = {
  primary: 'ui-btn-primary',
  secondary: 'ui-btn-secondary',
  ghost: 'ui-btn-ghost',
  danger: 'ui-btn-danger',
  icon: 'ui-btn-icon',
};

const ButtonRoot = forwardRef<HTMLButtonElement, ButtonProps>(function ButtonRoot(
  { variant = 'primary', icon, className, children, type = 'button', ...props },
  ref,
) {
  return (
    <button ref={ref} type={type} className={cn('ui-btn', variantClass[variant], className)} {...props}>
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

function Secondary(props: Omit<ButtonProps, 'variant'>) {
  return <ButtonRoot variant="secondary" {...props} />;
}

function Danger(props: Omit<ButtonProps, 'variant'>) {
  return <ButtonRoot variant="danger" {...props} />;
}

function Icon(props: Omit<ButtonProps, 'variant'>) {
  return <ButtonRoot variant="icon" {...props} />;
}

export const Button = Object.assign(ButtonRoot, { Primary, Secondary, Ghost, Danger, Icon });
