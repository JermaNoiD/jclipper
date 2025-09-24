            document.getElementById('generateButton').addEventListener('click', (e) => {
                console.log('Button clicked, submitting form');
                document.getElementById('generateForm').submit();
            });
            function updateResolution() {
                const scale = document.getElementById('scale').value;
                const origW = {{ original_res[0] }};
                const origH = {{ original_res[1] }};
                const newW = Math.round(origW * scale);
                const newH = Math.round(origH * scale);
                document.getElementById('resolution').textContent = `Scaled: ${newW}x${newH}`;
            }