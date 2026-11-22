import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto mt-16 max-w-lg rounded-2xl border bg-card p-8 text-center shadow-card">
      <div className="text-[11px] font-medium tracking-[0.18em] text-gold">404</div>
      <h1 className="mt-3 text-xl font-semibold">페이지를 찾을 수 없습니다</h1>
      <p className="mt-3 text-sm leading-6 text-muted-foreground">주소가 바뀌었거나, 데모 스냅샷에 없는 종목일 수 있습니다.</p>
      <Link href="/" className="mt-5 inline-flex rounded-full border border-gold/40 px-4 py-2 text-sm font-medium text-gold hover:bg-gold/10">대시보드로</Link>
    </div>
  );
}
