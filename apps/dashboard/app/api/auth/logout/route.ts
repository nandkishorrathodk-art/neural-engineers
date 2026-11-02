import { NextRequest, NextResponse } from 'next/server';

export async function POST(request: NextRequest) {
  const origin = request.headers.get('origin');
  if (origin) {
    try {
      const host = request.headers.get('host');
      const forwardedProtocol = request.headers.get('x-forwarded-proto')?.split(',')[0]?.trim();
      const protocol = forwardedProtocol || request.nextUrl.protocol.replace(':', '');
      const requestOrigin = host ? `${protocol}://${host}` : request.nextUrl.origin;
      if (new URL(origin).origin !== new URL(requestOrigin).origin) {
        return NextResponse.json({ detail: 'Cross-origin sign-out is not allowed.' }, { status: 403 });
      }
    } catch {
      return NextResponse.json({ detail: 'Invalid sign-out origin.' }, { status: 403 });
    }
  }

  const response = NextResponse.json({ authenticated: false }, { headers: { 'Cache-Control': 'no-store' } });
  response.cookies.set('ancos_owner_session', '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'strict',
    path: '/',
    maxAge: 0,
  });
  return response;
}
