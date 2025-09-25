
  function filterMovies() {
    const input = document.getElementById('searchInput').value.toLowerCase();
      const items = document.getElementsByClassName('movie-list-item');
      for (let i = 0; i < items.length; i++) {
        const name = items[i].dataset.name;
        items[i].style.display = name.includes(input) ? 'flex' : 'none';
      }
  }

