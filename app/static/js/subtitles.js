let startIndex = -1;
let endIndex = -1;
let currentHighlightIndex = -1;
const items = document.getElementsByClassName('subtitle-item');
const submitButton = document.getElementById('submitButton');
const startTimeInput = document.getElementById('startTime');
const endTimeInput = document.getElementById('endTime');

function handleSearchKey(event) {
    if (event.key === 'Enter') {
        navigateNext();
    } else {
        highlightSubtitles();
    }
}

function highlightSubtitles() {
    const searchTerm = document.getElementById('searchInput').value.toLowerCase();
    for (let i = 0; i < items.length; i++) {
        const contentDiv = items[i].getElementsByClassName('subtitle-content')[0];
        let content = items[i].dataset.originalContent;
        if (searchTerm) {
            const regex = new RegExp(`(${searchTerm})`, 'gi');
            content = content.replace(regex, '<span style="background-color: #FFFF00; color: black;">$1</span>');
        }
        contentDiv.innerHTML = content;
        if (i === startIndex) contentDiv.innerHTML += '<span style="color: #22C55E; font-weight: bold; margin-left: 10px;">&lt; Start</span>';
        if (i === endIndex) contentDiv.innerHTML += '<span style="color: #F87171; font-weight: bold; margin-left: 10px;">&lt; End</span>';
        items[i].classList.remove('disabled', 'bg-green-500', 'bg-red-400', 'bg-yellow-100', 'opacity-50', 'cursor-not-allowed');
        items[i].style.backgroundColor = '';
        items[i].style.pointerEvents = '';
        items[i].style.display = 'block';
        if (i === startIndex) items[i].classList.add('bg-green-500');
        if (i === endIndex) items[i].classList.add('bg-red-400');
        if (i === currentHighlightIndex) items[i].classList.add('bg-yellow-100');
        if (startIndex !== -1) {
            const startTime = items[startIndex].dataset.startTime;
            if (i < startIndex && compareTimes(items[i].dataset.startTime, startTime) < 0) {
                items[i].classList.add('opacity-50', 'cursor-not-allowed');
                items[i].style.pointerEvents = 'none';
            }
        }
    }
    submitButton.disabled = !(startIndex !== -1 && endIndex !== -1 && startTimeInput && endTimeInput && startTimeInput.value && endTimeInput.value);
}

function selectSubtitle(index) {
    const item = items[parseInt(index)];
    if (!item) return;
    const itemTime = item.dataset.startTime;
    if (!itemTime) return;
    if (startIndex === -1) {
        startIndex = parseInt(index);
        if (startTimeInput) startTimeInput.value = itemTime;
    } else if (endIndex === -1) {
        const startTime = items[startIndex].dataset.startTime;
        if (compareTimes(itemTime, startTime) >= 0) {
            endIndex = parseInt(index);
            const endTime = item.getElementsByTagName('strong')[0]?.textContent.split(' --> ')[1];
            if (endTime && endTimeInput) endTimeInput.value = endTime;
        } else {
            alert("End time cannot be earlier than start time. Please select a later timestamp.");
            return;
        }
    }
    highlightSubtitles();
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

function navigateNext() {
    const searchTerm = document.getElementById('searchInput').value.toLowerCase();
    if (!searchTerm || items.length === 0) return;

    let newIndex = currentHighlightIndex + 1;
    if (newIndex >= items.length) newIndex = 0;

    let found = false;
    let checkedAll = false;
    while (!found && !checkedAll) {
        const text = items[newIndex].textContent.toLowerCase();
        if (text.includes(searchTerm)) {
            found = true;
            currentHighlightIndex = newIndex;
            items[currentHighlightIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
            highlightSubtitles();
        }
        newIndex = (newIndex + 1) % items.length;
        if (newIndex === (currentHighlightIndex + 1) % items.length) checkedAll = true;
    }
}

function compareTimes(time1, time2) {
    const [h1, m1, s1] = time1.split(':').map(t => parseFloat(t.replace(',', '.')));
    const [h2, m2, s2] = time2.split(':').map(t => parseFloat(t.replace(',', '.')));
    const totalSeconds1 = h1 * 3600 + m1 * 60 + s1;
    const totalSeconds2 = h2 * 3600 + m2 * 60 + s2;
    return totalSeconds1 - totalSeconds2;
}