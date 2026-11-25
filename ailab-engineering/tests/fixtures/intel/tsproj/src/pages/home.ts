import Cart from '../components/Cart';
export async function home(name: string) {
  const lazy = await import('./lazy');
  const dyn = require(name);
  return [Cart, lazy, dyn];
}
