<script lang="ts">
  import type { Box, Page, Tool } from './types'

  let {
    page,
    tool,
    selected = $bindable(),
    onadd,
  }: {
    page: Page
    tool: Tool
    selected: number | null
    onadd: (box: Box) => void
  } = $props()

  let svgEl: SVGSVGElement | undefined
  let draft = $state<Box | null>(null)
  let dragStart: { x: number; y: number } | null = null

  const MIN_SIZE = 4 // ignore accidental tiny drags (image px)

  function clamp(v: number, lo: number, hi: number) {
    return Math.max(lo, Math.min(hi, v))
  }

  // Map a pointer event to the page image's pixel coordinates (the SVG viewBox
  // is the image's native size, so we scale by the displayed size).
  function toImg(e: PointerEvent): { x: number; y: number } {
    const r = svgEl!.getBoundingClientRect()
    return {
      x: clamp(((e.clientX - r.left) / r.width) * page.width, 0, page.width),
      y: clamp(((e.clientY - r.top) / r.height) * page.height, 0, page.height),
    }
  }

  function onPointerDown(e: PointerEvent) {
    if (tool !== 'draw') return
    e.preventDefault()
    svgEl!.setPointerCapture(e.pointerId)
    const p = toImg(e)
    dragStart = p
    draft = [p.x, p.y, p.x, p.y]
  }

  function onPointerMove(e: PointerEvent) {
    if (!dragStart || tool !== 'draw') return
    const p = toImg(e)
    draft = [dragStart.x, dragStart.y, p.x, p.y]
  }

  function onPointerUp(e: PointerEvent) {
    if (!dragStart) return
    const p = toImg(e)
    const x0 = Math.min(dragStart.x, p.x)
    const y0 = Math.min(dragStart.y, p.y)
    const x1 = Math.max(dragStart.x, p.x)
    const y1 = Math.max(dragStart.y, p.y)
    dragStart = null
    draft = null
    if (x1 - x0 >= MIN_SIZE && y1 - y0 >= MIN_SIZE) {
      onadd([Math.round(x0), Math.round(y0), Math.round(x1), Math.round(y1)])
    }
  }

  function onRectClick(i: number, e: MouseEvent) {
    if (tool === 'select') {
      e.stopPropagation()
      selected = i
    }
  }
</script>

<div class="editor" class:draw={tool === 'draw'}>
  <img src={`data:image/jpeg;base64,${page.image}`} alt="Document page" />
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_noninteractive_element_interactions -->
  <svg
    bind:this={svgEl}
    viewBox={`0 0 ${page.width} ${page.height}`}
    preserveAspectRatio="none"
    onpointerdown={onPointerDown}
    onpointermove={onPointerMove}
    onpointerup={onPointerUp}
    onclick={() => tool === 'select' && (selected = null)}
    role="application"
    aria-label="Redaction box editor"
  >
    {#each page.boxes as box, i (i)}
      <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
      <rect
        x={box[0]}
        y={box[1]}
        width={box[2] - box[0]}
        height={box[3] - box[1]}
        class="box"
        class:selected={selected === i}
        onclick={(e) => onRectClick(i, e)}
      />
    {/each}
    {#if draft}
      <rect
        x={Math.min(draft[0], draft[2])}
        y={Math.min(draft[1], draft[3])}
        width={Math.abs(draft[2] - draft[0])}
        height={Math.abs(draft[3] - draft[1])}
        class="draft"
      />
    {/if}
  </svg>
</div>

<style>
  .editor {
    position: relative;
    display: inline-block;
    max-width: 100%;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    line-height: 0;
  }
  .editor.draw svg {
    cursor: crosshair;
  }
  img {
    display: block;
    max-width: 100%;
    height: auto;
  }
  svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    touch-action: none;
  }
  .box {
    fill: rgba(0, 0, 0, 0.55);
    stroke: #f59e0b;
    stroke-width: 1.5;
    vector-effect: non-scaling-stroke;
    cursor: pointer;
  }
  .box.selected {
    fill: rgba(0, 0, 0, 0.35);
    stroke: #ef4444;
    stroke-width: 2.5;
    stroke-dasharray: 5 3;
    vector-effect: non-scaling-stroke;
  }
  .draft {
    fill: rgba(37, 99, 235, 0.2);
    stroke: #2563eb;
    stroke-width: 1.5;
    stroke-dasharray: 4 3;
    vector-effect: non-scaling-stroke;
  }
</style>
