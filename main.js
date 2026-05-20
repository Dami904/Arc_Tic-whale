// static/main.js

// The base URL of your API.
// When running locally, this will be your localtunnel URL.
const API_BASE_URL = window.location.origin;

// Function to update the color of the percentage change based on its value
function updateChangeColor(elementId, value) {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = value;
        if (value.startsWith('-')) {
            element.classList.remove('positive');
            element.classList.add('negative');
        } else {
            element.classList.remove('negative');
            element.classList.add('positive');
        }
    }
}

// Function to fetch and update the wallet stats
async function fetchWalletStats() {
    try {
        const response = await fetch(`${API_BASE_URL}/stats`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const stats = await response.json();

        // Update total balance
        const balanceElement = document.getElementById('wallet-balance');
        if (balanceElement) {
            // Format to 2 decimal places and add commas
            balanceElement.textContent = `$${stats.total_balance_usd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        }

        // Update performance metrics
        updateChangeColor('24h-change', stats.performance['24h']);
        updateChangeColor('7d-change', stats.performance['7d']);
        updateChangeColor('1y-change', stats.performance['1y']);

    } catch (error) {
        console.error("Could not fetch wallet stats:", error);
    }
}

// Run the function when the page loads and then every 30 seconds
window.addEventListener('load', () => {
    fetchWalletStats();
    setInterval(fetchWalletStats, 30000);
});