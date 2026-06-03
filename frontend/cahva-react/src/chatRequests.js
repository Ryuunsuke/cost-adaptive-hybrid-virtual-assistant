/**
 * Module-level store for in-flight chat fetch promises.
 *
 * Lives outside React so promises survive component unmount/remount cycles.
 * When a user switches sessions mid-generation and returns, the Chat component
 * can re-attach to the pending promise and show the response when it arrives.
 */
const _pending = new Map(); // sessionId (string) → Promise<response>

const chatRequests = {
  set(sessionId, promise) {
    _pending.set(String(sessionId), promise);
  },
  get(sessionId) {
    return _pending.get(String(sessionId)) ?? null;
  },
  has(sessionId) {
    return _pending.has(String(sessionId));
  },
  clear(sessionId) {
    _pending.delete(String(sessionId));
  },
};

export default chatRequests;
