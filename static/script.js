function updateSensorDisplay() {
  console.log("センサーデータ取得開始");
  fetch("/api/sensor_status")
    .then(res => {
      console.log("レスポンスステータス:", res.status);
      return res.json();
    })
    .then(data => {
      console.log("受信データ:", data);
      const container = document.getElementById("sensor-data");
      if (data && "temperature" in data) {
        container.innerHTML = `
          <ul>
            <li>時刻: ${data.timestamp}</li>
            <li>温度: ${data.temperature.toFixed(2)} ℃</li>
            <li>湿度: ${data.humidity.toFixed(2)} %</li>
          </ul>
        `;
      } else {
        container.innerHTML = "<p>センサーデータ未受信</p>";
        console.warn("データに temperature が含まれていません");
      }
    })
    .catch(err => {
      console.error("センサーデータ取得失敗:", err);
    });
}

function updateStatus() {
  console.log("在室状態取得開始");
  fetch("/api/status", { cache: "no-store" })
    .then(res => {
      console.log("在室ステータス:", res.status);
      return res.json();
    })
    .then(data => {
      console.log("在室データ:", data);
      const tbody = document.querySelector("#status-table tbody");
      tbody.innerHTML = "";
      data.students.forEach(s => {
        const row = document.createElement("tr");
        row.innerHTML = `
          <td>${s.name}</td>
          <td class="status-${s.status}">${s.status}</td>
          <td>${s.room}</td>
          <td>${s.entry}</td>
          <td>${s.duration}</td>
        `;
        tbody.appendChild(row);
      });
    })
    .catch(err => {
      console.error("? 在室状態取得失敗:", err);
    });
}

async function updateAlertBadge() {
    try {
        const response = await fetch('/api/alerts_count');
        
        // ユーザーがログアウトした場合やサーバーエラーの場合は何もしない
        if (!response.ok) {
            return; 
        }

        const data = await response.json();
        const count = data.count;
        
        const badge = document.getElementById('alert-badge');
        if (!badge) return; // ページにバッジ要素がなければ終了

        // 件数に基づいてバッジの表示を切り替える
        if (count > 0) {
            badge.textContent = count;
            badge.classList.remove('hidden'); // hiddenクラスを削除して表示
        } else {
            badge.textContent = '0';
            badge.classList.add('hidden'); // hiddenクラスを追加して非表示
        }

    } catch (error) {
        // ネットワークエラーなどでフェッチに失敗した場合
        console.error('Error fetching alert count:', error);
        const badge = document.getElementById('alert-badge');
        if (badge) {
            badge.textContent = '?'; // エラー表示
            badge.classList.remove('hidden');
        }
    }
}

console.log("script.js 読み込まれました");

document.addEventListener("DOMContentLoaded", () => {
  console.log("DOM構築完了");
  updateSensorDisplay();
  updateStatus();
  updateAlertBadge();
  setInterval(updateSensorDisplay, 1000);
  setInterval(updateStatus, 1000);
  setInterval(updateAlertBadge,5000);
});
