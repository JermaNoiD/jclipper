let startIndex = -1;
let endIndex = -1;
let currentHighlightIndex = -1;
const items = document.getElementsByClassName('subtitle-item');
const submitButton = document.getElementById('submitButton');
const startTimeInput = document.getElementById('startTime');
const endTimeInput = document.getElementById('endTime');

function toggleSelection(element) {
    const index = Array.from(items).indexOf(element);
    selectSubtitle(index);
}

function handleSearchKey(event) {
    if (event.key === 'Enter') {
        navigateNext();
    } else {
        highlightSubtitles();
        autoScrollToFirstMatch();
    }
}

function highlightSubtitles() {
    const searchTerm = document.getElementById('searchInput')?.value.toLowerCase() || '';
    for (let i = 0; i < items.length; i++) {
        const contentDiv = items[i].getElementsByClassName('subtitle-content')[0];
        let content = items[i].dataset.originalContent;
        contentDiv.innerHTML = content; // Reset to original content
        if (searchTerm && content.toLowerCase().includes(searchTerm)) {
            const regex = new RegExp(`(${searchTerm})`, 'gi');
            contentDiv.innerHTML = content.replace(regex, '<span class="highlight">$1</span>');
        }
        // Remove all background classes
        items[i].classList.remove('bg-green-100', 'dark:bg-green-900', 'bg-red-100', 'dark:bg-red-900', 'bg-gradient-green-red', 'bg-yellow-100', 'dark:bg-yellow-700', 'opacity-50', 'cursor-not-allowed');
        items[i].style.pointerEvents = '';
        items[i].style.display = 'block';
        // Apply selection highlights
        if (i === startIndex && i === endIndex) {
            items[i].classList.add('bg-gradient-green-red');
            contentDiv.innerHTML += '<span style="color: #22C55E; font-weight: bold; margin-left: 10px;">&lt; Start/End</span>';
        } else {
            if (i === startIndex) {
                items[i].classList.add('bg-green-100', 'dark:bg-green-900');
                contentDiv.innerHTML += '<span style="color: #22C55E; font-weight: bold; margin-left: 10px;">&lt; Start</span>';
            }
            if (i === endIndex) {
                items[i].classList.add('bg-red-100', 'dark:bg-red-900');
                contentDiv.innerHTML += '<span style="color: #F87171; font-weight: bold; margin-left: 10px;">&lt; End</span>';
            }
        }
        if (i === currentHighlightIndex) {
            items[i].classList.add('bg-yellow-100', 'dark:bg-yellow-700');
        }
        // Disable earlier subtitles if start is selected
        if (startIndex !== -1) {
            const startTime = items[startIndex].dataset.startTime;
            if (i < startIndex && compareTimes(items[i].dataset.startTime, startTime) < 0) {
                items[i].classList.add('opacity-50', 'cursor-not-allowed');
                items[i].style.pointerEvents = 'none';
            }
        }
    }
    // Enable/disable submit button
    submitButton.disabled = !(startIndex !== -1 && endIndex !== -1 && startTimeInput && endTimeInput && startTimeInput.value && endTimeInput.value);
}

function selectSubtitle(index) {
    const item = items[index];
    if (!item) return;
    const startTime = item.dataset.startTime;
    const endTime = item.dataset.endTime;
    if (!startTime || !endTime) return;
    if (startIndex === -1) {
        startIndex = index;
        if (startTimeInput) startTimeInput.value = startTime;
    } else if (endIndex === -1) {
        const startTimeCheck = items[startIndex].dataset.startTime;
        if (compareTimes(endTime, startTimeCheck) >= 0) {
            endIndex = index;
            if (endTimeInput) endTimeInput.value = endTime;
        } else {
            alert("End time cannot be earlier than start time. Please select a later timestamp.");
            return;
        }
    } else {
        // Reset selection if both start and end are already selected
        startIndex = index;
        endIndex = -1;
        if (startTimeInput) startTimeInput.value = startTime;
        if (endTimeInput) endTimeInput.value = '';
    }
    highlightSubtitles();
    console.log(`Selected: start=${startTimeInput?.value}, end=${endTimeInput?.value}`); // Debug log
}

function clearSelections() {
    startIndex = -1;
    endIndex = -1;
    currentHighlightIndex = -1;
    if (startTimeInput) startTimeInput.value = '';
    if (endTimeInput) endTimeInput.value = '';
    submitButton.disabled = true;
    highlightSubtitles();
}

function createClip() {
    if (!submitButton.disabled) {
        document.getElementById('subtitle-form').submit();
    }
}

function navigateNext() {
    const searchTerm = document.getElementById('searchInput')?.value.toLowerCase() || '';
    if (!searchTerm || items.length === 0) return;

    let newIndex = (currentHighlightIndex + 1) % items.length;
    let found = false;
    let checkedAll = false;

    while (!found && !checkedAll) {
        const content = items[newIndex].dataset.originalContent.toLowerCase();
        if (content.includes(searchTerm)) {
            found = true;
            currentHighlightIndex = newIndex;
            items[currentHighlightIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
            highlightSubtitles();
        }
        newIndex = (newIndex + 1) % items.length;
        if (newIndex === (currentHighlightIndex + 1) % items.length) checkedAll = true;
    }
}

function autoScrollToFirstMatch() {
    const searchTerm = document.getElementById('searchInput')?.value.toLowerCase() || '';
    if (!searchTerm || items.length === 0) return;

    for (let i = 0; i < items.length; i++) {
        const content = items[i].dataset.originalContent.toLowerCase();
        if (content.includes(searchTerm)) {
            currentHighlightIndex = i;
            items[currentHighlightIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
            highlightSubtitles();
            break;
        }
    }
}

function compareTimes(time1, time2) {
    const [h1, m1, s1] = time1.split(':').map(t => parseFloat(t.replace(',', '.')));
    const [h2, m2, s2] = time2.split(':').map(t => parseFloat(t.replace(',', '.')));
    const totalSeconds1 = h1 * 3600 + m1 * 60 + s1;
    const totalSeconds2 = h2 * 3600 + m2 * 60 + s2;
    return totalSeconds1 - totalSeconds2;
}

// Initialize
window.addEventListener('load', () => {
    if (items.length > 0 && startTimeInput && !startTimeInput.value && endTimeInput && !endTimeInput.value) {
        startTimeInput.value = items[0].dataset.startTime;
        endTimeInput.value = items[0].dataset.endTime;
    }
    highlightSubtitles();
});