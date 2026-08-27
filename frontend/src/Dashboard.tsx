export default function Dashboard() {
  return (
    <div className="dashboard">
      <header className="dashboard-heading">
        <div>
          <span className="section-kicker">OPERATIONS</span>
          <h1>운영 상황판</h1>
          <p>장애 신호 API가 준비되면 실제 운영 데이터를 표시합니다.</p>
        </div>
        <span className="updated-at">연동 대기</span>
      </header>
      <section className="dashboard-card state-card">
        장애 신호 백엔드가 아직 구현되지 않아 표시할 운영 데이터가 없습니다.
      </section>
    </div>
  );
}
