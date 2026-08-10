import { MessageList } from './MessageList';
import { ChatComposer } from './ChatComposer';

export function ChatMain() {
  return (
    <main className="chat">
      <MessageList />
      <ChatComposer />
    </main>
  );
}
