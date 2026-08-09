<script lang="ts">
  import { i18n, setLocale, t, type Locale } from './i18n.svelte'

  const m = $derived(t())

  // Each option names its own language, so a user who landed on the wrong one can
  // still read the way out.
  const LANGUAGES: { value: Locale; label: string }[] = [
    { value: 'en', label: 'English' },
    { value: 'de', label: 'Deutsch' },
  ]
</script>

<select
  value={i18n.locale}
  aria-label={m.settings.language}
  title={m.settings.language}
  onchange={(e) => setLocale(e.currentTarget.value as Locale)}
>
  {#each LANGUAGES as language}
    <option value={language.value} lang={language.value}>{language.label}</option>
  {/each}
</select>

<style>
  select {
    font: inherit;
    font-size: 0.8rem;
    color: var(--fg);
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.15rem 0.3rem;
    cursor: pointer;
    vertical-align: middle;
  }
  select:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>
