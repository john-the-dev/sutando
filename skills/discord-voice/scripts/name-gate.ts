// Multi-bot voice name-gate — pure decision logic, no I/O.
// See notes/multi-bot-voice-gate-redesign.md for the full design + test matrix.

export interface GateConfig {
	/** This instance's stand name, e.g. "Lucy". Empty disables the gate. */
	instanceName: string;
	/** Spoken-form aliases for instanceName (ASR variants). */
	nameAliases?: string[];
	/** Other instances' canonical names. */
	otherInstances?: string[];
	/** Spoken-form aliases for OTHER instances. */
	otherAliases?: string[];
	/** When true (and no name in transcript yet), respond to cold openers. */
	primary?: boolean;
	/**
	 * Meeting-buddy mode (single bot, multiple humans). When true the gate:
	 *   - starts SILENT (ignores `primary`) — waits to be named before answering,
	 *   - stays active even with no peer instances (own-name is the only wake),
	 *   - honors `standbyAliases` as an explicit "go silent but stay" command.
	 * See notes/multi-bot-voice-gate-redesign.md + PR #1427.
	 */
	meetingMode?: boolean;
	/** Spoken-form "standby / go quiet" phrases that re-silence the bot. */
	standbyAliases?: string[];
}

export type Decision = 'allow' | 'drop';

export interface GateState {
	readonly cfg: GateConfig;
	readonly nameVariants: string[];
	readonly otherVariants: string[];
	readonly standbyVariants: string[];
	/** Sticky last-addressed-to-me bit. */
	lastAddressedToMe: boolean;
}

export const ADDRESS_VERBS =
	'(can|could|will|would|please|tell|answer|design|write|read|check|look|help|stop|start|leave|join|log|hang|end)';

/**
 * Detect whether `text` ADDRESSES (not just mentions) any of `names`.
 * Returns true on:
 *   - "hi/hey/hello/yo/okay/ok NAME" or "hi, NAME" (greet + name)
 *   - "NAME," or "NAME?" (comma/question-tag — definite address marker)
 *   - "NAME VERB" at sentence start (imperative)
 * Returns false on plain mentions like "thanks NAME" or "NAME's answer".
 */
export function isAddressedBy(text: string, names: string[]): boolean {
	if (!text || names.length === 0) return false;
	const lc = text.toLowerCase();
	for (const raw of names) {
		const n = raw.toLowerCase().trim();
		if (!n) continue;
		// "hi/hey/... NAME" or "hi, NAME" — greeting allows optional punctuation
		const greet = new RegExp(`\\b(hi|hey|hello|yo|okay|ok)[,!:]?\\s+${escape(n)}\\b`, 'i');
		// "NAME," or "NAME?" — comma/question-tag (definite address marker)
		const commaTag = new RegExp(`\\b${escape(n)}\\s*[,?]`, 'i');
		// "NAME VERB" at sentence start (optional preceding . ! ?)
		const verbed = new RegExp(`(^|[.!?]\\s*)${escape(n)}\\s+${ADDRESS_VERBS}\\b`, 'i');
		if (greet.test(lc) || commaTag.test(lc) || verbed.test(lc)) return true;
	}
	return false;
}

/**
 * Open-world "addressed to someone other than me" detector. Catches greet/
 * comma/verb patterns that name ANY token not in `myNames` — so the operator
 * can say "Hi Bob" or "Hi Daddy" without us having to enumerate every alias
 * in the OTHER list. Stopwords filter pronouns/question-words/short verbs to
 * avoid false-positive on "you can hear me", "what time is it", etc.
 *
 * Anchored patterns (commaTag + verbed require sentence-start `(^|[.!?]\s*)`)
 * are intentional — without that, "is this math?" matches as "math?" address.
 */
export function isAddressedToOther(text: string, myNames: string[]): boolean {
	if (!text) return false;
	const myLc = myNames.map(n => n.toLowerCase().trim()).filter(Boolean);
	const lc = text.toLowerCase();
	const patterns = [
		// greet+name anywhere (also allow 1-2 word names like "Maddy Lou")
		/\b(hi|hey|hello|yo|okay|ok)[,!:]?\s+([a-z][a-z'-]*(?:\s+[a-z][a-z'-]*)?)\b/gi,
		// commaTag — require start-of-clause before name
		/(^|[.!?]\s*)([a-z][a-z'-]*)\s*[,?]/gi,
		// imperative — start-of-clause + name + verb
		new RegExp(`(^|[.!?]\\s*)([a-z][a-z'-]*)\\s+${ADDRESS_VERBS}\\b`, 'gi'),
	];
	for (const re of patterns) {
		let m;
		while ((m = re.exec(lc)) !== null) {
			// Group containing the captured name varies by pattern; find non-empty
			const name = (m[2] || m[1] || '').trim();
			if (!name || _STOPWORDS.has(name)) continue;
			const isMe = myLc.some(v => v === name || name.startsWith(v + ' '));
			if (!isMe) return true;
		}
	}
	return false;
}

const _STOPWORDS = new Set([
	// pronouns + question words
	'i', 'me', 'my', 'mine', 'you', 'your', 'yours', 'we', 'us', 'our', 'ours',
	'they', 'them', 'their', 'theirs', 'he', 'him', 'his', 'she', 'her', 'hers',
	'it', 'its', 'this', 'that', 'these', 'those', 'there', 'here',
	'who', 'whom', 'whose', 'what', 'which', 'when', 'where', 'why', 'how',
	// affirmations / acknowledgements
	'yes', 'no', 'yep', 'nope', 'yeah', 'nah', 'okay', 'ok', 'sure', 'right',
	'thanks', 'thank', 'please', 'sorry', 'maybe',
	// fillers / interjections
	'oh', 'um', 'uh', 'well', 'so', 'just', 'now', 'still', 'also',
	'and', 'or', 'but', 'if', 'as',
	// common short verbs that often start clauses
	'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had',
	'do', 'does', 'did', 'go', 'goes', 'went', 'come', 'came',
	'one', 'two', 'three', 'a', 'an', 'the', 'all', 'some', 'any',
]);

/** Construct initial gate state. */
export function createGate(cfg: GateConfig): GateState {
	const nameVariants = [cfg.instanceName, ...(cfg.nameAliases ?? [])]
		.map(s => s.trim()).filter(Boolean);
	const otherVariants = [...(cfg.otherInstances ?? []), ...(cfg.otherAliases ?? [])]
		.map(s => s.trim()).filter(Boolean);
	const standbyVariants = (cfg.standbyAliases ?? [])
		.map(s => s.trim()).filter(Boolean);
	return {
		cfg,
		nameVariants,
		otherVariants,
		standbyVariants,
		// Meeting mode starts SILENT — the bot waits to be named, regardless of
		// `primary`. Legacy (non-meeting) mode keeps the primary-driven default.
		lastAddressedToMe: cfg.meetingMode ? false : !!cfg.primary,
	};
}

/**
 * Detect an explicit "standby / go quiet" command. Standby phrases are bare
 * command words ("standby", "待命"), not name-addressed imperatives, so this is
 * a word-boundary (ASCII) / substring (CJK) match rather than isAddressedBy.
 */
export function isStandby(text: string, standbyVariants: string[]): boolean {
	if (!text || standbyVariants.length === 0) return false;
	const lc = text.toLowerCase();
	for (const raw of standbyVariants) {
		const s = raw.toLowerCase().trim();
		if (!s) continue;
		const ascii = /^[\x00-\x7f]+$/.test(s);
		if (ascii ? new RegExp(`\\b${escape(s)}\\b`, 'i').test(lc) : lc.includes(s)) return true;
	}
	return false;
}

/**
 * Process one user-turn's transcript text and return the new decision.
 * Updates state in-place. Sticky semantics:
 *   - standby phrase → drop (sticky=false): go silent but stay in the room.
 *   - my-name addressed → allow (sticky=true)
 *   - other-name addressed → drop (sticky=false)
 *   - neither → unchanged (sticky carries)
 *
 * Meeting mode (single bot, multiple humans): the gate stays active even with
 * NO peer instances — own-name is the only wake, standby is the only sleep —
 * and (via createGate) starts silent. Legacy mode keeps the old "no peer →
 * always allow" shortcut so non-meeting single-bot deployments are unchanged.
 */
export function decideForTurn(state: GateState, userText: string): Decision {
	// Standby: explicit "go silent but stay" — re-silences until next name cue.
	if (isStandby(userText, state.standbyVariants)) {
		state.lastAddressedToMe = false;
		return 'drop';
	}
	// Legacy single-bot (no peers, not meeting mode): gate disabled, allow all.
	if (!state.cfg.meetingMode && state.otherVariants.length === 0) return 'allow';
	const haveMyName = isAddressedBy(userText, state.nameVariants);
	const haveOtherName = state.otherVariants.length > 0 && isAddressedBy(userText, state.otherVariants);
	if (haveMyName) state.lastAddressedToMe = true;
	else if (haveOtherName) state.lastAddressedToMe = false;
	return state.lastAddressedToMe ? 'allow' : 'drop';
}

// --- internals ---

function escape(s: string): string {
	return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
