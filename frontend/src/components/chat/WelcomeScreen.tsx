import { useChat } from '@/app/providers/ChatProvider';
import { CodeBracketsIcon } from '@/components/ui/Icons';

const SUGGESTIONS = [
  {
    title: 'Deploy nginx with Helm',
    detail: 'Production-ready Helm chart for nginx',
    prompt: 'Deploy nginx using Helm in production',
  },
  {
    title: 'Explain a k8s module',
    detail: 'Parameters and examples for kubernetes.core.k8s',
    prompt: 'Explain the kubernetes.core.k8s module',
  },
  {
    title: 'Scale a deployment',
    detail: 'Set replica count to 3 safely',
    prompt: 'Scale a deployment to 3 replicas',
  },
  {
    title: 'Node maintenance',
    detail: 'Drain a node before maintenance work',
    prompt: 'Drain a node for maintenance',
  },
  {
    title: 'Helm vs raw manifests',
    detail: 'When to choose each approach',
    prompt: 'What is the difference between k8s and helm?',
  },
  {
    title: 'Copy into a pod',
    detail: 'Transfer files with the copy module',
    prompt: 'Copy a file to a pod',
  },
];

export function WelcomeScreen() {
  const { setSuggestText } = useChat();

  return (
    <div className="welcome">
      <div className="welcome-hero">
        <div className="welcome-orb">
          <CodeBracketsIcon size={24} />
        </div>
        <h1>What do you want to automate?</h1>
        <p>Describe an infrastructure task in plain language. AnsibleAI drafts playbooks using your indexed docs.</p>
      </div>

      <div className="welcome-grid">
        {SUGGESTIONS.map((item, i) => (
          <button
            key={item.title}
            type="button"
            className="welcome-card"
            style={{ animationDelay: `${80 + i * 55}ms` }}
            onClick={() => setSuggestText(item.prompt)}
          >
            <span className="welcome-card-title">{item.title}</span>
            <span className="welcome-card-detail">{item.detail}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
