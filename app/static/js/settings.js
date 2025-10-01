document.getElementById('generateButton').addEventListener('click', (e) => {
    console.log('Button clicked, submitting form');
    document.getElementById('generateForm').submit();
});

document.querySelector('form').id = 'generate-form';
    
// Update padding value dynamically
const paddingInput = document.querySelector('input[name="padding"]');
const paddingSpan = document.getElementById('padding-value');
paddingInput.addEventListener('input', function() {
    paddingSpan.textContent = this.value;
});

// Update scaled resolution dynamically
const scaleInput = document.querySelector('input[name="scale_factor"]');
const scaledSpan = document.getElementById('scaled-resolution');
function updateResolution() {
    fetch(`/resolution?scale=${scaleInput.value}`)
        .then(response => response.json())
        .then(data => {
            scaledSpan.textContent = data.scaled;
        });
}
scaleInput.addEventListener('input', updateResolution);
// Initialize resolution on page load
updateResolution();

function startPulse() {
    console.log('Starting pulse animation');
    const formContainer = document.getElementById('formContainer');
    formContainer.classList.add('pulse-animation');
}