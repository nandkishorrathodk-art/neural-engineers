import { NextRequest, NextResponse } from 'next/server';

const SESSION_COOKIE = 'ancos_owner_session';

export function middleware(request: NextRequest) {
  const suppliedSession = request.cookies.get(SESSION_COOKIE)?.value;
  if (suppliedSession) return NextResponse.next();

  if (request.nextUrl.pathname.startsWith('/api/')) {
    return NextResponse.json({ detail: 'Owner sign-in required.' }, { status: 401 });
  }

  const loginUrl = new URL('/login', request.url);
  loginUrl.searchParams.set('returnTo', request.nextUrl.pathname);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ['/((?!login|api/auth|_next/static|_next/image|favicon.ico).*)'],
};
