/**
 * A one-time, polite GitHub star ask. This module imports nothing from `vscode`
 * so it can be unit-tested; extension.ts passes the editor APIs in.
 */

export const REPO_URL = 'https://github.com/qualixar/superlocalmemory';
export const STAR_FLAG = 'slm.starAskShown';
export const STAR_MESSAGE = 'Is SuperLocalMemory useful? A GitHub star helps other developers find it.';
export const STAR_ACTION = 'Star on GitHub';
export const LATER_ACTION = 'Not now';
export const STAR_DELAY_MS = 45_000;

export interface StarAskHost {
  /** Reads the persisted flag (context.globalState.get). */
  readonly getFlag: (key: string) => boolean | undefined;
  /** Persists the flag (context.globalState.update). */
  readonly setFlag: (key: string, value: boolean) => PromiseLike<void>;
  /** Shows the information message and resolves to the chosen action, if any. */
  readonly showMessage: (message: string, ...actions: string[]) => PromiseLike<string | undefined>;
  /** Opens a URL in the user's browser (vscode.env.openExternal). */
  readonly openUrl: (url: string) => PromiseLike<unknown>;
}

/**
 * Shows the ask once ever. The flag is set as soon as the message is shown, so a
 * dismissed message (Escape or the close button) is not asked again either.
 * Returns true when the message was shown. Never throws.
 */
export async function askForStar(host: StarAskHost): Promise<boolean> {
  try {
    if (host.getFlag(STAR_FLAG)) {
      return false;
    }
    await host.setFlag(STAR_FLAG, true);
    const choice = await host.showMessage(STAR_MESSAGE, STAR_ACTION, LATER_ACTION);
    if (choice === STAR_ACTION) {
      await host.openUrl(REPO_URL);
    }
    return true;
  } catch {
    return false;
  }
}

/** Schedules the ask after a short delay; the returned function cancels it. */
export function scheduleStarAsk(host: StarAskHost, delayMs: number = STAR_DELAY_MS): () => void {
  const timer = setTimeout(() => {
    void askForStar(host);
  }, delayMs);
  return () => clearTimeout(timer);
}
