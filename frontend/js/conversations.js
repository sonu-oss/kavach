// Real Conversation Store for Kavach.
// Default is empty: chats are added when scanned via Kavach Shield extension or screenshot import.
const daysAgo = (n) => {
  const d = new Date(Date.now() - n * 86400000);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
};

window.SEED_CONVERSATIONS = [];

window.SAMPLE_INCIDENTS = [
  {
    id: "sample_blackmail",
    incident_date: daysAgo(0),
    platform: "WhatsApp",
    name: "+91 90637 51560",
    subtitle: "Extortion / Blackmail",
    avatar: "!",
    messages: [
      {
        sender: "+91 90637 51560",
        text: "Don't reply to this message i want you to arrange 2 crores within a week or i am going to expose your photos to your wife if you try to go to police your story will be in headlines think and say to me i will call at 11:54 today be sure to pick up",
        time: "10:25",
      },
    ],
  },
  {
    id: "sample_stalking",
    incident_date: daysAgo(0),
    platform: "WhatsApp",
    name: "Unknown Stalker",
    subtitle: "Physical stalking threat",
    avatar: "S",
    messages: [
      { sender: "stalker", text: "I know where you live. #42, 5th Cross. You were wearing a yellow kurta at the bus stop.", time: "18:40" },
      { sender: "me", text: "Who is this? Stop following me.", time: "18:42" },
      { sender: "stalker", text: "Don't block me. I will be waiting outside your college tomorrow morning.", time: "18:45" },
    ],
  },
];
