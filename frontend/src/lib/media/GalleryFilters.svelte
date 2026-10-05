<script lang="ts">
	import type { GalleryState } from './gallery.svelte';
	import { workflowNames } from './workflowNames.svelte';
	let { gallery, onapply }: { gallery: GalleryState; onapply: (event: SubmitEvent) => void } =
		$props();
</script>

<form id="gallery-filters" class="field-grid" onsubmit={onapply}>
	<label>
		<span>Type</span>
		<select bind:value={gallery.mediaKind}>
			<option value="">All media</option>
			<option value="image">Images</option>
			<option value="video">Videos</option>
			<option value="audio">Audio</option>
			<option value="other">Other</option>
		</select>
	</label>
	<label>
		<span>Favorite</span>
		<select bind:value={gallery.favorite}>
			<option value="">All</option>
			<option value="true">Favorites</option>
			<option value="false">Not favorites</option>
		</select>
	</label>
	<label>
		<span>Workflow</span>
		<select bind:value={gallery.workflowId}>
			<option value="">Any workflow</option>
			{#each workflowNames.list as workflow (workflow.id)}
				<option value={workflow.id}>{workflow.name}</option>
			{/each}
		</select>
	</label>
	<label>
		<span>From</span>
		<input type="date" bind:value={gallery.createdAfter} />
	</label>
	<label>
		<span>Through</span>
		<input type="date" bind:value={gallery.createdBefore} />
	</label>
	<label>
		<span>Search in</span>
		<select
			value={gallery.searchField}
			onchange={(event) => gallery.setSearchField(event.currentTarget.value)}
		>
			<option value="any">Any metadata / filename</option>
			<option value="prompt">Prompt / text inputs</option>
			<option value="model">Model / checkpoint inputs</option>
			<option value="seed">Seed</option>
			<option value="workflow">Workflow name</option>
			<option value="filename">Filename</option>
		</select>
	</label>
	<label>
		<span>Collection</span>
		<select bind:value={gallery.collectionId}>
			<option value="">Any collection</option>
			{#each gallery.collections as collection (collection.id)}
				<option value={collection.id}>{collection.name}</option>
			{/each}
		</select>
	</label>
	<label class="field-wide">
		<span>Saved metadata</span>
		<input
			value={gallery.prompt}
			oninput={(event) => gallery.setPrompt(event.currentTarget.value)}
			list="gallery-suggestions"
			maxlength="500"
			autocomplete="off"
			placeholder="Search retained values"
			aria-describedby="search-scope"
		/>
		<datalist id="gallery-suggestions">
			{#each gallery.suggestions as value (value)}<option {value}></option>{/each}
		</datalist>
		<small id="search-scope"
			>Searches the whole library by literal substring, including filenames and retained inputs.</small
		>
	</label>
</form>
