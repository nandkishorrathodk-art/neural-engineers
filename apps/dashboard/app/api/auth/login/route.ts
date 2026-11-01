import { timingSafeEqual } from 'node:crypto';
import { NextRequest, NextResponse } from 'next/server';

const SESSION_COOKIE = 'ancos_owner_session';
const SESSION_MAX_AGE_SECONDS = 6 * 60 * 60;

function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get('origin');
  if (!origin) return true;
  try {
    return new URL(origin).origin === request.nextUrl.origin;
  } catch {
    return false;
  }
}

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) {
    return NextResponse.json({ detail: 'Cross-origin sign-in is not allowed.' }, { status: 403 });
  }

  const configuredToken = process.env.OWNER_API_TOKEN;
  if (!configuredToken || configuredToken.length < 32) {
    return NextResponse.json(
      { detail: 'Dashboard owner authentication is not configured with a 32-character token.' },
      { status: 503, headers: { 'Cache-Control': 'no-store' } },
    );
  }

  let suppliedToken = '';
  try {
    const payload = await request.json();
    suppliedToken = typeof payload?.token === 'string' ? payload.token : '';
  } catch {
    return NextResponse.json({ detail: 'Invalid sign-in request.' }, { status: 400 });
  }

  const expectedBytes = Buffer.from(configuredToken);
  const suppliedBytes = Buffer.from(suppliedToken);
  if (expectedBytes.length !== suppliedBytes.length || !timingSafeEqual(expectedBytes, suppliedBytes)) {
    return NextResponse.json(
      { detail: 'Invalid owner token.' },
      { status: 401, headers: { 'Cache-Control': 'no-store' } },
    );
  }

  const response = NextResponse.json({ authenticated: true }, { headers: { 'Cache-Control': 'no-store' } });
  response.cookies.set(SESSION_COOKIE, configuredToken, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'strict',
    path: '/',
    maxAge: SESSION_MAX_AGE_SECONDS,
  });
  return response;
}
