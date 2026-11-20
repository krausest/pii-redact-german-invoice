<script module lang="ts">
  export interface Lists {
    redactWords: string[]
    keepWords: string[]
  }
</script>

<script lang="ts">
  import { t } from './i18n.svelte'
  import { MAX_WORD_LENGTH, MAX_WORDS } from './types'

  type Key = keyof Lists

  let {
    lists,
    onChange,
    onRefresh,
    pending = false,
    disabled = false,
  }: {
    lists: Lists
    onChange: (next: Lists) => void
    /** Absent with no document loaded: there is nothing to re-analyze. */
    onRefresh?: () => void
    /** The lists differ from the ones the documents were analyzed with. */
    pending?: boolean
    disabled?: boolean
  } = $props()

  const m = $derived(t())
  const columns = $derived([
    { key: 'redactWords' as Key, other: 'keepWords' as Key, title: m.wordlists.redactTitle, hint: m.wordlists.redactHint },
    { key: 'keepWords' as Key, other: 'redactWords' as Key, title: m.wordlists.keepTitle, hint: m.wordlists.keepHint },
  ])

  let draft = $state<Record<Key, string>>({ redactWords: '', keepWords: '' })
  let error = $state<Record<Key, string | null>>({ redactWords: null, keepWords: null })

  const has = (list: string[], word: string) => list.some((w) => w.toLocaleLowerCase() === word.toLocaleLowerCase())

  function problem(key: Key, other: Key, word: string): string | null {
    if (word.length > MAX_WORD_LENGTH) return m.wordlists.tooLong(MAX_WORD_LENGTH)
    if (has(lists[key], word)) return m.wordlists.duplicate
    const otherTitle = other === 'keepWords' ? m.wordlists.keepTitle : m.wordlists.redactTitle
    if (has(lists[other], word)) return m.wordlists.inOther(otherTitle)
    if (lists[key].length >= MAX_WORDS) return m.wordlists.tooMany(MAX_WORDS)
    return null
  }

  function add(e: SubmitEvent, key: Key, other: Key) {
    e.preventDefault()
    const word = draft[key].trim().replace(/\s+/g, ' ')
    if (!word) return
    error[key] = problem(key, other, word)
    if (error[key]) return
    onChange({ ...lists, [key]: [...lists[key], word] })
    draft[key] = ''
  }

  function remove(key: Key, index: number) {
    onChange({ ...lists, [key]: lists[key].filter((_, i) => i !== index) })
  }
</script>

<section class="wordlists" aria-label={m.wordlists.group}>
  {#each columns as col (col.key)}
    <div class="column">
      <h2>{col.title}</h2>
      <p class="hint">{col.hint}</p>
      <form onsubmit={(e) => add(e, col.key, col.other)}>
        <input
          type="text"
          bind:value={draft[col.key]}
          oninput={() => (error[col.key] = null)}
          placeholder={m.wordlists.placeholder}
          aria-label={m.wordlists.addTo(col.title)}
          maxlength={MAX_WORD_LENGTH}
          {disabled}
        />
        <button type="submit" disabled={disabled || !draft[col.key].trim()}>{m.wordlists.add}</button>
      </form>
      {#if error[col.key]}<p class="error" role="alert">{error[col.key]}</p>{/if}
      <ul aria-label={col.title}>
        {#each lists[col.key] as word, i (word)}
          <li>
            <span>{word}</span>
            <button
              class="remove"
              onclick={() => remove(col.key, i)}
              {disabled}
              title={m.wordlists.remove(word)}
              aria-label={m.wordlists.remove(word)}
            >×</button>
          </li>
        {:else}
          <li class="empty">{m.wordlists.empty}</li>
        {/each}
      </ul>
    </div>
  {/each}
  {#if onRefresh}
    <div class="refresh">
      {#if pending}<span class="pending">{m.wordlists.pending}</span>{/if}
      <button class="primary" onclick={onRefresh} disabled={disabled || !pending} title={m.wordlists.refreshTitle}>
        {m.wordlists.refresh}
      </button>
    </div>
  {/if}
</section>

<style>
  .wordlists {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.6rem;
    margin-bottom: 0.9rem;
    text-align: left;
  }
  @media (max-width: 600px) {
    .wordlists {
      grid-template-columns: 1fr;
    }
  }
  .refresh {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 0.6rem;
  }
  .pending {
    color: var(--muted);
    font-size: 0.8rem;
  }
  button.primary {
    background: var(--accent);
    color: var(--accent-fg);
    border-color: transparent;
  }
  button.primary:hover:not(:disabled) {
    filter: brightness(1.08);
  }
  .column {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    box-shadow: var(--shadow);
    padding: 0.6rem 0.75rem;
    min-width: 0;
  }
  h2 {
    margin: 0;
    font-size: 0.9rem;
  }
  .hint {
    margin: 0.15rem 0 0.5rem;
    color: var(--muted);
    font-size: 0.78rem;
  }
  form {
    display: flex;
    gap: 0.4rem;
  }
  input {
    flex: 1;
    min-width: 0;
    font: inherit;
    font-size: 0.85rem;
    color: var(--fg);
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.35rem 0.5rem;
  }
  button {
    font: inherit;
    font-size: 0.85rem;
    font-weight: 600;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--fg);
    padding: 0.35rem 0.7rem;
    cursor: pointer;
  }
  button:disabled,
  input:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }
  input:focus-visible,
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .error {
    margin: 0.3rem 0 0;
    color: var(--error);
    font-size: 0.78rem;
  }
  ul {
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    max-height: 6.5rem;
    overflow-y: auto;
  }
  li {
    display: inline-flex;
    align-items: center;
    gap: 0.2rem;
    max-width: 100%;
    font-size: 0.82rem;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.1rem 0.15rem 0.1rem 0.6rem;
  }
  li span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  li.empty {
    border: none;
    background: none;
    padding: 0.1rem 0;
    color: var(--muted);
  }
  .remove {
    border: none;
    background: none;
    padding: 0 0.35rem;
    border-radius: 999px;
    color: var(--muted);
    font-size: 1rem;
    line-height: 1;
  }
  .remove:hover:not(:disabled) {
    color: var(--error);
  }
</style>
