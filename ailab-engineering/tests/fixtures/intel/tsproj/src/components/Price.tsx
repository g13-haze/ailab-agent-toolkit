import { fmt } from '@/lib/format';
export default function Price(p: {c: number}) { return fmt(p.c); }
