export const pieceNames = {p:'pawn',n:'knight',b:'bishop',r:'rook',q:'queen',k:'king'};
const shapes = {
 p: '<circle cx="32" cy="17" r="8"/><path d="M27 26h10l-2 7c1 7 5 11 8 14H21c3-3 7-7 8-14z"/><path d="M20 47h24l3 7H17z"/>',
 n: '<path d="M19 49c0-10 7-15 15-20l-10 3-6-5 10-9 1-10 9 8c8 1 11 7 10 15-1 7-7 10-9 18z"/><path d="M19 49h25l3 5H16z"/><path d="M28 18l7 1" fill="none"/><circle cx="35" cy="23" r="1.9" class="piece-eye" stroke="none"/>',
 b: '<path d="M32 9c-5 6-12 10-12 17 0 6 5 9 12 9s12-3 12-9c0-7-7-11-12-17z"/><path d="M32 14l-5 10" fill="none"/><path d="M27 36h10l2 10H25z"/><path d="M22 46h20l5 8H17z"/><circle cx="32" cy="8" r="2.5"/>',
 r: '<path d="M19 12h7v7h5v-7h7v7h5v-7h4v15H19z"/><path d="M23 27h20l-3 19H26z"/><path d="M21 46h24l3 8H18z"/><path d="M24 30h18" fill="none"/>',
 q: '<path d="M17 20l8 7 7-13 7 13 8-7-6 21H23z"/><circle cx="16" cy="17" r="3"/><circle cx="32" cy="11" r="3"/><circle cx="48" cy="17" r="3"/><path d="M23 41h18v6H23z"/><path d="M21 47h22l5 7H16z"/><path d="M25 35h14" fill="none"/>',
 k: '<path d="M29 8h6v6h6v5h-6v7h-6v-7h-6v-5h6z"/><path d="M32 28c-10-9-21 0-12 12l4 4h16l4-4c9-12-2-21-12-12z"/><path d="M24 44h16v5H24z"/><path d="M21 49h22l5 5H16z"/><path d="M32 28v12" fill="none"/>'
};
export function pieceSVG(piece) {
 const white=piece===piece.toUpperCase();
 return `<svg class="chess-piece ${white?'white-piece':'black-piece'}" aria-hidden="true" viewBox="0 0 64 64"><g stroke-linejoin="round" stroke-linecap="round" stroke-width="2.4">${shapes[piece.toLowerCase()]||''}</g></svg>`;
}
export function parseFEN(fen) {
 const position={};
 if(!fen) return position;
 const rows=fen.split(' ')[0].split('/');
 rows.forEach((row,index)=>{let file=0;for(const c of row){if(/\d/.test(c))file+=Number(c);else{position[String.fromCharCode(97+file)+(8-index)]=c;file++;}}});
 return position;
}
export function miniBoard(fen) {
 const position=parseFEN(fen);
 return `<div class="mini-board" aria-hidden="true">${Array.from({length:64},(_,i)=>{const file=i%8,rank=8-Math.floor(i/8),p=position[String.fromCharCode(97+file)+rank];return `<span class="${(file+rank)%2===1?'dark':'light'}">${p?pieceSVG(p):''}</span>`;}).join('')}</div>`;
}
