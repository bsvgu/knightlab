import {parseFEN, pieceSVG} from './pieces.js';

const MOVE_MS = 160;
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Coordinates always come from the rendered squares, so flipping and resizing
// use the same path as an ordinary move. The server supplies each accepted FEN.
export class BoardMotion {
 constructor(board, options) {
  this.board = board;
  this.options = options;
  this.pointer = null;
  this.held = null;
  this.suppressUntil = 0;
  board.addEventListener('pointerdown', event => this.down(event));
  board.addEventListener('pointermove', event => this.move(event));
  board.addEventListener('pointerup', event => this.up(event));
  board.addEventListener('pointercancel', () => this.cancel());
  window.addEventListener('pointerup', event => {
   if (this.pointer?.id === event.pointerId && !this.pointer.active) this.cancel();
  });
  board.addEventListener('lostpointercapture', event => {
   if (event.target === board && this.pointer?.active) this.cancel();
  });
  board.addEventListener('dragstart', event => event.preventDefault());
  window.addEventListener('blur', () => { if (this.pointer) this.cancel(); });
  window.addEventListener('keydown', event => {
   if (event.key === 'Escape' && this.pointer) this.cancel();
  });
 }
 get dragging() { return !!this.pointer?.active; }
 get occupied() { return !!this.pointer || !!this.held; }
 square(name) { return this.board.querySelector(`[data-square="${name}"]`); }
 suppressClick(event) { return event.detail !== 0 && performance.now() < this.suppressUntil; }
 down(event) {
  if (!event.isPrimary || event.button !== 0 || this.occupied || !this.options.enabled() || !this.options.canInteract()) return;
  const square = event.target.closest('[data-square]')?.dataset.square;
  const position = this.options.getBoard();
  if (!square || !(position.legal_moves || []).some(move => move.startsWith(square))) return;
  this.pointer = {id:event.pointerId, from:square, x:event.clientX, y:event.clientY, active:false};
 }
 move(event) {
  const pointer = this.pointer;
  if (!pointer || event.pointerId !== pointer.id) return;
  if (!pointer.active) {
   if (Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) < 6) return;
   this.board.setPointerCapture(pointer.id);
   this.options.onSelect(pointer.from);
   const piece = parseFEN(this.options.getBoard().fen)[pointer.from];
   if (!piece) { this.cancel(); return; }
   this.held = this.makeGhost(pointer.from, piece);
   pointer.active = true;
   this.board.classList.add('dragging');
  }
  event.preventDefault();
  this.place(this.held, event.clientX - this.held.width / 2, event.clientY - this.held.height / 2);
  this.clearTargets();
  const target = this.targetAt(event.clientX, event.clientY);
  if (target && (this.options.getBoard().legal_moves || []).some(move => move.startsWith(pointer.from + target))) this.square(target)?.classList.add('drop-target');
 }
 targetAt(x, y) {
  const target = document.elementFromPoint(x, y)?.closest('[data-square]');
  return target && this.board.contains(target) ? target.dataset.square : null;
 }
 async up(event) {
  const pointer = this.pointer;
  if (!pointer || pointer.id !== event.pointerId) return;
  const target = this.targetAt(event.clientX, event.clientY);
  this.pointer = null;
  if (this.board.hasPointerCapture(pointer.id)) this.board.releasePointerCapture(pointer.id);
  if (!pointer.active) return;
  const held = this.held;
  event.preventDefault();
  this.suppressUntil = performance.now() + 500;
  this.board.classList.remove('dragging');
  this.clearTargets();
  const legal = target && (this.options.getBoard().legal_moves || []).some(move => move.startsWith(pointer.from + target));
  try {
   if (legal && this.options.canInteract()) await this.options.onDrop(pointer.from, target);
   else await this.revert();
  } finally {
   this.removeGhost(held);
   if (this.held === held) this.held = null;
   this.options.onSettled();
  }
 }
 clearTargets() { this.board.querySelectorAll('.drop-target').forEach(square => square.classList.remove('drop-target')); }
 syncSource() { if (this.held) this.square(this.held.from)?.querySelector('.chess-piece')?.classList.add('piece-in-flight'); }
 makeGhost(from, piece) {
  const rect = this.square(from).getBoundingClientRect();
  const node = document.createElement('div');
  node.className = 'piece-motion';
  node.setAttribute('aria-hidden', 'true');
  node.style.width = `${rect.width}px`;
  node.style.height = `${rect.height}px`;
  node.innerHTML = pieceSVG(piece);
  document.body.append(node);
  const ghost = {node, from, width:rect.width, height:rect.height, x:rect.left, y:rect.top};
  this.place(ghost, rect.left, rect.top);
  this.square(from)?.querySelector('.chess-piece')?.classList.add('piece-in-flight');
  return ghost;
 }
 place(ghost, x, y) {
  ghost.x = x; ghost.y = y;
  ghost.node.style.transform = `translate(${x}px, ${y}px)`;
 }
 async travel(ghost, target, duration = MOVE_MS) {
  const rect = this.square(target)?.getBoundingClientRect();
  if (!rect || !ghost) return;
  if (!reducedMotion()) {
   const animation = ghost.node.animate([
    {transform:`translate(${ghost.x}px, ${ghost.y}px)`},
    {transform:`translate(${rect.left}px, ${rect.top}px)`}
   ], {duration, easing:'cubic-bezier(.2,.7,.3,1)', fill:'forwards'});
   try { await animation.finished; } catch { /* A cancelled drag is already cleaned up. */ }
   animation.cancel();
  }
  this.place(ghost, rect.left, rect.top);
 }
 removeGhost(ghost) {
  if (!ghost) return;
  ghost.node.getAnimations().forEach(animation => animation.cancel());
  ghost.node.remove();
  if (!this.held || this.held === ghost || this.held.from !== ghost.from) this.square(ghost.from)?.querySelector('.chess-piece')?.classList.remove('piece-in-flight');
 }
 async revert() {
  const held = this.held;
  if (!held) return;
  await this.travel(held, held.from);
  this.removeGhost(held);
  if (this.held === held) this.held = null;
 }
 cancel() {
  const pointer = this.pointer;
  this.pointer = null;
  if (pointer && this.board.hasPointerCapture(pointer.id)) this.board.releasePointerCapture(pointer.id);
  if (pointer?.active) this.suppressUntil = performance.now() + 500;
  this.removeGhost(this.held);
  this.held = null;
  this.clearTargets();
  this.board.classList.remove('dragging');
  if (pointer?.active) this.options.onSettled();
 }
 async animateMove(frame, beforeFEN, render) {
  const before = parseFEN(beforeFEN), after = parseFEN(frame.fen);
  const from = frame.uci.slice(0, 2), to = frame.uci.slice(2, 4);
  const piece = before[from];
  if (!piece || !this.square(from) || !this.square(to)) { render(frame); return; }
  const primary = this.held?.from === from ? this.held : this.makeGhost(from, piece);
  const ghosts = [{ghost:primary, to}];
  // In castling the rook travels at the same time as the king.
  if (piece.toLowerCase() === 'k' && Math.abs(from.charCodeAt(0) - to.charCodeAt(0)) === 2) {
   const rookFrom = (to[0] === 'g' ? 'h' : 'a') + from[1];
   const rookTo = (to[0] === 'g' ? 'f' : 'd') + from[1];
   if (before[rookFrom]?.toLowerCase() === 'r' && after[rookTo] === before[rookFrom]) ghosts.push({ghost:this.makeGhost(rookFrom, before[rookFrom]), to:rookTo});
  }
  try {
   await Promise.all(ghosts.map(({ghost, to}) => this.travel(ghost, to, ghost === this.held ? 80 : MOVE_MS)));
  } finally {
   ghosts.forEach(({ghost}) => this.removeGhost(ghost));
   if (primary === this.held) this.held = null;
  }
  render(frame);
 }
}
